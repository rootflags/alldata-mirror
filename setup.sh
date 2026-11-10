#!/bin/sh
#
# Initial setup script
#
# Creates ~/.alldata-mirror/alldata-mirror.conf from the repo's alldata-mirror.conf if you don't have one yet,
# then walks you through the settings you're likely to change (where mirrors go, the pause between pages).
# Run it again any time to review them; Enter keeps the current value.  Then it checks the software
# alldata-mirror needs, creates the folders, and checks your cookies file (see README.md for copying it).
#
# Without a terminal (ie: "./setup.sh < /dev/null") it doesn't ask or install anything: it creates the config
# and folders and lists any missing software.
#

FOLDER=`cd \`dirname $0\` && pwd`
CONFIG_DIR=${HOME}/.alldata-mirror
CFG=${CONFIG_DIR}/alldata-mirror.conf

# get KEY: KEY's value in $CFG ("key = value" lines; the first one wins)
get() {
	sed -n "s/^$1[ 	]*=[ 	]*//p" "${CFG}" | head -1 | sed -e 's/[ 	]*$//'
}

# set_value KEY VALUE: replace the first "KEY = ..." line in $CFG
set_value() {
	SET_KEY="$1" SET_VALUE="$2" awk '
		!done && $0 ~ "^" ENVIRON["SET_KEY"] "[ \t]*=" { print ENVIRON["SET_KEY"] " = " ENVIRON["SET_VALUE"]; done = 1; next }
		{ print }' "${CFG}" > "${CFG}.new" && cat "${CFG}.new" > "${CFG}"
	rm -f "${CFG}.new"
}

# expand PATH: ~ and ~/... as your home folder
expand() {
	case "$1" in
		"~") echo "${HOME}" ;;
		"~/"*) echo "${HOME}/${1#\~/}" ;;
		*) echo "$1" ;;
	esac
}

# The pause between pages may only be made longer than the template's (the README asks the same)
DEFAULT_DELAY=`sed -n 's/^delay[ 	]*=[ 	]*//p' "${FOLDER}/alldata-mirror.conf" | head -1 | sed -e 's/[ 	]*$//'`

# delay_ok VALUE: VALUE is N or MIN-MAX seconds, and neither end is below the default's
delay_ok() {
	printf '%s' "$1" | grep -Eq '^[0-9]+(\.[0-9]+)?(-[0-9]+(\.[0-9]+)?)?$' || return 1
	awk -v v="$1" -v d="${DEFAULT_DELAY}" 'BEGIN {
		if (split(v, a, "-") == 1) a[2] = a[1]
		if (split(d, b, "-") == 1) b[2] = b[1]
		exit !(a[1] + 0 >= b[1] + 0 && a[2] + 0 >= b[2] + 0 && a[2] + 0 >= a[1] + 0) }'
}

# ask KEY QUESTION: show KEY's current value and read a new one into ANSWER.  Enter keeps the current value.
# Returns 1 if the value didn't change.
ask() {
	CUR=`get $1`
	printf '%s\n    [%s]: ' "$2" "${CUR}"
	read -r ANSWER || ANSWER=
	[ x"${ANSWER}" = x ] && ANSWER="${CUR}"
	[ x"${ANSWER}" != x"${CUR}" ]
}

if [ -f "${CFG}" ]; then
	echo "Using your settings in ${CFG}"
	NEW=0
else
	echo "Creating ${CFG} from ${FOLDER}/alldata-mirror.conf"
	mkdir -p "${CONFIG_DIR}"
	( umask 077; sed -e '/^# ---- TEMPLATE ONLY/,/^# ---- (setup.sh leaves/d' "${FOLDER}/alldata-mirror.conf" > "${CFG}" )
	NEW=1
fi

if [ -t 0 ]; then
	REVIEW=y
	if [ ${NEW} = 0 ]; then
		printf 'Review your settings now? [y/N] '
		read -r REVIEW || REVIEW=n
	fi
	case "${REVIEW}" in
	[yY]*)
		echo
		echo "Press Enter to keep the value in [brackets], or type a new one."
		echo
		ask output "Where should mirrors go?  Each vehicle gets its own folder in it.  To publish them, use a folder your web server serves." &&
			set_value output "${ANSWER}"
		echo
		while ask delay "Seconds to wait before following each link: a number, or MIN-MAX for a random pause.  It can only be made longer than the default, ${DEFAULT_DELAY}"; do
			if delay_ok "${ANSWER}"; then
				set_value delay "${ANSWER}"
				break
			fi
			echo "    Please give a number of seconds, or MIN-MAX, no lower than ${DEFAULT_DELAY} (ie: ${DEFAULT_DELAY} or 8-10)"
		done
		echo
		echo "Saved.  The settings are described in ${CFG}"
		;;
	esac
elif [ ${NEW} = 1 ]; then
	echo "** Check the settings in ${CFG} (at least output), or run $0 in a terminal to be asked"
fi

# A config edited by hand (or from an older setup.sh) mustn't go faster than the default either
DELAY=`get delay`
if ! delay_ok "${DELAY}"; then
	echo "** delay = ${DELAY} in ${CFG} is faster than the default (or not valid); setting it back to ${DEFAULT_DELAY}"
	set_value delay "${DEFAULT_DELAY}"
fi

OUTPUT=`expand "\`get output\`"`
COOKIES=`expand "\`get cookies\`"`
[ x"${COOKIES}" = x ] && COOKIES=${CONFIG_DIR}/cookies.txt

# private FILE MODE: make FILE readable only by you (MODE, ie: 600), saying so if it wasn't.  The cookies and
# the session file are a live login to your ALLDATA account.
private() {
	[ -e "$1" ] || return
	[ x"`ls -ld "$1" | cut -c5-10`" = x------ ] && return
	echo "Making $1 private"
	chmod $2 "$1"
}
private "${CONFIG_DIR}" 700
private "${CFG}" 600
private "${COOKIES}" 600
private "${CONFIG_DIR}/session.json" 600

# ---- Dependencies -------------------------------------------------------------------------------
# Only Python 3.10 or newer, with the sqlite3 and ssl modules most builds include.

PM=""
for P in apt-get dnf pacman zypper brew; do
	if which ${P} > /dev/null 2>&1; then PM=${P}; break; fi
done
SUDO=""
[ `id -u` != 0 ] && [ "${PM}" != brew ] && SUDO="sudo "
case "${PM}" in
	apt-get) INSTALL="${SUDO}apt-get install -y python3" ;;
	dnf) INSTALL="${SUDO}dnf install -y python3" ;;
	pacman) INSTALL="${SUDO}pacman -S --needed python" ;;
	zypper) INSTALL="${SUDO}zypper install -y python3" ;;
	brew) INSTALL="brew install python" ;;
	*) INSTALL="" ;;
esac

check() {
	PROBLEM=""
	if ! which python3 > /dev/null 2>&1; then
		PROBLEM="python3 is not installed"
	elif ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
		PROBLEM="python3 is `python3 -V 2>&1 | cut -d' ' -f2`; alldata-mirror needs 3.10 or newer"
	elif ! python3 -c 'import sqlite3, ssl' 2>/dev/null; then
		PROBLEM="this python3 lacks the sqlite3 or ssl module"
	fi
}

echo "Checking dependencies..."
check
if [ -n "${PROBLEM}" ]; then
	echo "** ${PROBLEM}"
	if [ -n "${INSTALL}" ]; then
		echo "   To install it: ${INSTALL}"
		if [ -t 0 ]; then
			printf 'Install it now? [y/N] '
			read -r ANSWER || ANSWER=n
			case "${ANSWER}" in
				[yY]*)
					[ "${PM}" = apt-get ] && ${SUDO}apt-get update
					${INSTALL}
					check ;;
			esac
		fi
	else
		echo "   Install Python 3.10 or newer with your system's package manager"
	fi
	if [ -n "${PROBLEM}" ]; then
		echo "** alldata-mirror can't run until that's fixed.  Run $0 again afterwards."
		exit 1
	fi
fi
echo "    `python3 -V 2>&1`: OK"

echo "Creating folders..."
mkdir -p "${OUTPUT}" || exit 1
echo "    Mirrors go to ${OUTPUT}"

# ---- Cookies ------------------------------------------------------------------------------------
echo "Checking your cookies..."
if [ ! -s "${COOKIES}" ]; then
	echo "** No cookies yet in ${COOKIES}.  alldata-mirror uses your browser's login:"
	echo "   1. Log in to https://my.alldata.com in Edge or Chrome and open your vehicle"
	echo "   2. Right-click the page, Inspect, then Application > Storage > Cookies > https://my.alldata.com"
	echo "   3. Click in the cookie list, select all (Ctrl-A), copy (Ctrl-C), and paste it all into"
	echo "      ${COOKIES}"
	echo "   Then run $0 again to check it."
else
	COOKIES="${COOKIES}" python3 - <<'EOF'
import base64, json, os, time
path = os.environ["COOKIES"]
jar = {}
for line in open(path, encoding="utf-8", errors="replace"):
    f = line.rstrip("\n").split("\t")
    if len(f) >= 2:
        jar[f[0]] = f[1]
token = jar.get("Access-Token")
if not token:
    print("** %s has no Access-Token cookie.  Copy the whole cookie list (see README.md), not just" % path)
    print("   some of it, from a browser that's logged in to my.alldata.com")
    raise SystemExit
try:
    part = token.split(".")[1]
    exp = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"]
except Exception:
    print("    Found an Access-Token cookie (can't tell when it expires)")
    raise SystemExit
when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exp))
if exp < time.time():
    print("** Your login expired on %s.  Log in again and copy fresh cookies into %s" % (when, path))
else:
    print("    %d cookies; your login is good until %s" % (len(jar), when))
EOF
fi

echo
echo "Setup is done.  Next, find your vehicle's ID in its my.alldata.com address"
echo "(https://my.alldata.com/repair/#/vehicle/57903 is vehicle 57903), then:"
echo "    python3 ${FOLDER}/crawl.py <vehicle-id> --max-pages 10      (a short test)"
echo "    python3 ${FOLDER}/crawl.py <vehicle-id>                     (the whole vehicle, many hours)"
echo "    python3 ${FOLDER}/build.py <vehicle-id>                     (the website, in ${OUTPUT}/<vehicle-id>/site/)"
