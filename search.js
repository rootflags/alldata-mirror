/*
 * search.js - The search box in the header of every page of the mirror (copied to site/search.js by build.py)
 *
 * Searches the index build.py writes to site/search/, loading only the pieces it needs as you type:
 * search/meta.js, the word list for the first two letters of each word typed (search/w/P0.js) and the
 * titles of the pages it shows (search/p/N.js).  Every word typed must start a word on the page (P04 finds
 * P0420); titles and whole words rank first.  As results scroll into view it fetches each page to show the
 * text around the match (only when the mirror is served over http; opened as files, results have no snippet).
 * Index files load with <script> tags, so it works on any plain web server and from disk.
 */
(function () {
	"use strict";
	var form = document.getElementById("search");
	if (!form) {
		return;
	}
	var input = form.querySelector("input");
	var panel = document.getElementById("search-results");
	var root = form.getAttribute("data-root");      // relative path from this page to the site root
	var SHOW = 50;                                  // results listed at a time
	var meta = null, haveShard = {}, shards = {}, chunks = {}, scripts = {};
	var seq = 0, timer = null, snippetQueue = [], snippetsRunning = 0, observer = null;

	// The index files call these when they load
	window.MirrorSearch = {
		meta: function (m) {
			meta = m;
			for (var i = 0; i < m.shards.length; i += 2) {
				haveShard[m.shards.substr(i, 2)] = true;
			}
		},
		words: function (key, w) { shards[key] = w; },
		pages: function (n, p) { chunks[n] = p; }
	};

	// Load search/PATH with a <script> tag (once).  Resolves false if it couldn't, so a later search tries again.
	function load(path) {
		if (!scripts[path]) {
			scripts[path] = new Promise(function (resolve) {
				var s = document.createElement("script");
				s.src = root + "search/" + path;
				s.onload = function () { resolve(true); };
				s.onerror = function () { delete scripts[path]; resolve(false); };
				document.head.appendChild(s);
			});
		}
		return scripts[path];
	}

	function tokens(q) {
		return (q.toUpperCase().match(/[A-Z0-9]+/g) || []).filter(function (t) { return t.length > 1; });
	}

	// "f7,8,1" (gaps between page numbers, in hex) -> [247, 255, 256]
	function pageList(gaps) {
		var out = [], n = 0;
		if (gaps) {
			gaps.split(",").forEach(function (g) { n += parseInt(g, 16); out.push(n); });
		}
		return out;
	}

	// {page: score} for the pages with a word starting with TOKEN; a whole word or a title scores higher
	function matches(token) {
		var found = {}, words = shards[token.substr(0, 2)] || {};
		Object.keys(words).forEach(function (w) {
			if (w.lastIndexOf(token, 0) !== 0) {
				return;
			}
			var exact = w === token ? 2 : 1;
			pageList(words[w][0]).forEach(function (p) { found[p] = Math.max(found[p] || 0, exact); });
			pageList(words[w][1]).forEach(function (p) { found[p] = Math.max(found[p] || 0, exact + 4); });
		});
		return found;
	}

	function escapeHtml(s) {
		return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]; });
	}

	function wordRegex(toks, flags) {
		return new RegExp("\\b(" + toks.join("|") + ")[A-Za-z0-9]*", flags);
	}

	// TEXT as HTML with the words starting with a token marked
	function highlight(text, toks) {
		var re = wordRegex(toks, "gi"), out = "", last = 0, m;
		while ((m = re.exec(text)) !== null) {
			out += escapeHtml(text.slice(last, m.index)) + "<mark>" + escapeHtml(m[0]) + "</mark>";
			last = re.lastIndex;
		}
		return out + escapeHtml(text.slice(last));
	}

	function show(on) {
		panel.hidden = !on;
	}

	function status(html) {
		panel.innerHTML = "<div class='s-status'>" + html + "</div>";
	}

	function search() {
		var q = input.value, toks = tokens(q), my = ++seq;
		if (!q.trim()) {
			show(false);
			return;
		}
		show(true);
		if (!toks.length) {
			status("Type at least 2 letters or digits");
			return;
		}
		status("Searching&hellip;");
		load("meta.js").then(function (ok) {
			if (!ok || !meta) {
				throw new Error("the search index didn't load");
			}
			var needed = [];
			toks.forEach(function (t) {
				var key = t.substr(0, 2);
				if (haveShard[key] && needed.indexOf(key) < 0) {
					needed.push(key);
				}
			});
			return Promise.all(needed.map(function (k) { return load("w/" + k + ".js"); }));
		}).then(function () {
			if (my !== seq) {
				return;
			}
			// Pages with every word, best first, then in the vehicle's order
			var score = null;
			toks.forEach(function (t) {
				var m = matches(t), next = {};
				Object.keys(m).forEach(function (p) {
					if (score === null || p in score) {
						next[p] = (score === null ? 0 : score[p]) + m[p];
					}
				});
				score = next;
			});
			var hits = Object.keys(score).map(Number).sort(function (a, b) { return score[b] - score[a] || a - b; });
			if (!hits.length) {
				status("No pages match <b>" + escapeHtml(q) + "</b>");
				return;
			}
			status(hits.length + " page" + (hits.length === 1 ? "" : "s"));
			snippetQueue = [];
			if (observer) {
				observer.disconnect();
			}
			list(hits, 0, toks, my);
		}).catch(function (e) {
			if (my === seq) {
				status("Search isn't working: " + escapeHtml(e.message) + ". Try again.");
			}
		});
	}

	// Show hits FROM to FROM + SHOW, with a button for the next lot
	function list(hits, from, toks, my) {
		var some = hits.slice(from, from + SHOW), needed = [];
		some.forEach(function (p) {
			var c = Math.floor(p / meta.chunk);
			if (needed.indexOf(c) < 0) {
				needed.push(c);
			}
		});
		Promise.all(needed.map(function (c) { return load("p/" + c + ".js"); })).then(function () {
			if (my !== seq) {
				return;
			}
			var more = panel.querySelector("button");
			if (more) {
				more.parentNode.removeChild(more);
			}
			some.forEach(function (p) {
				var chunk = chunks[Math.floor(p / meta.chunk)];
				if (!chunk) {
					return;
				}
				var row = chunk.pages[p % meta.chunk], div = document.createElement("div");
				div.className = "s-hit";
				div.innerHTML = "<a class='s-title' href='" + escapeHtml(root + row[0]) + "'>" + highlight(row[1], toks) +
					"</a><div class='s-section'>" + escapeHtml(chunk.sections[row[2]]) + "</div><div class='s-snippet'></div>";
				panel.appendChild(div);
				if (row[3] && location.protocol !== "file:") {
					watch(div, root + row[0], toks, my);
				}
			});
			if (from + SHOW < hits.length) {
				var button = document.createElement("button");
				button.type = "button";
				button.textContent = "Show " + Math.min(SHOW, hits.length - from - SHOW) + " more";
				button.onclick = function () { list(hits, from + SHOW, toks, my); };
				panel.appendChild(button);
			}
		});
	}

	// The text around the match, fetched once the result scrolls into view, 3 pages at a time
	function watch(div, href, toks, my) {
		var job = function () { return snippet(div, href, toks, my); };
		if (!window.IntersectionObserver) {
			queue(job);
			return;
		}
		if (!observer) {
			observer = new IntersectionObserver(function (entries) {
				entries.forEach(function (e) {
					if (e.isIntersecting && e.target.searchJob) {
						queue(e.target.searchJob);
						e.target.searchJob = null;
						observer.unobserve(e.target);
					}
				});
			}, { root: panel });
		}
		div.searchJob = job;
		observer.observe(div);
	}

	function queue(job) {
		snippetQueue.push(job);
		run();
	}

	function run() {
		while (snippetsRunning < 3 && snippetQueue.length) {
			snippetsRunning++;
			snippetQueue.shift()().then(function () {
				snippetsRunning--;
				run();
			});
		}
	}

	function snippet(div, href, toks, my) {
		if (my !== seq) {
			return Promise.resolve();
		}
		return fetch(href).then(function (r) { return r.ok ? r.text() : ""; }).then(function (page) {
			var doc = new DOMParser().parseFromString(page, "text/html");
			var main = doc.querySelector("main");
			if (!main) {
				return;
			}
			// Not the breadcrumbs, styles or the search box
			Array.prototype.forEach.call(main.querySelectorAll("script, style, .crumbs"), function (n) {
				n.parentNode.removeChild(n);
			});
			var text = main.textContent.replace(/\s+/g, " ").trim();
			var m = wordRegex(toks, "i").exec(text);
			if (!m) {
				return;
			}
			var start = Math.max(0, m.index - 60), end = Math.min(text.length, m.index + 120);
			while (start > 0 && text.charAt(start - 1) !== " ") {
				start++;
			}
			while (end < text.length && text.charAt(end) !== " ") {
				end++;
			}
			div.querySelector(".s-snippet").innerHTML = (start > 0 ? "&hellip;" : "") +
				highlight(text.slice(start, end), toks) + (end < text.length ? "&hellip;" : "");
		}).catch(function () { /* no snippet, the result still works */ });
	}

	input.addEventListener("input", function () {
		clearTimeout(timer);
		timer = setTimeout(search, 250);
	});
	input.addEventListener("keydown", function (e) {
		if (e.key === "Escape") {
			input.value = "";
			search();
			input.blur();
		} else if (e.key === "Enter") {
			e.preventDefault();
			clearTimeout(timer);
			search();
		}
	});
	input.addEventListener("focus", function () {
		if (input.value.trim()) {
			show(true);
		}
	});
	// Clicking anywhere else closes the results; focusing the box again reopens them
	document.addEventListener("click", function (e) {
		if (!form.contains(e.target) && !panel.contains(e.target)) {
			show(false);
		}
	});
	// "/" jumps to the search box
	document.addEventListener("keydown", function (e) {
		if (e.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
			e.preventDefault();
			input.focus();
		}
	});
	if (input.value) {
		search();
	}
})();
