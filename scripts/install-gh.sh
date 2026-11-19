#!/bin/bash
# 安全安装 GitHub 官方命令行工具 gh（不用 brew，不用管理员密码）。
#
#   bash scripts/install-gh.sh            安装下面钉死的版本
#   bash scripts/install-gh.sh --latest   安装官方当前最新版（以后升级用）
#   bash scripts/install-gh.sh --check    只体检：不下载、不改任何东西
#
# 安装前过三道核对，任何一道不过就停，什么都不会装：
#   1. 来源    只从 github.com/cli/cli 的官方发布页下载，全程 HTTPS
#   2. 完整性  压缩包的 SHA-256（文件指纹）要和官方清单一致；
#              钉死的版本还要和本脚本里记下的指纹一致（两个来源互相印证）
#   3. 身份    程序本体要带「苹果签发给 GitHub 的开发者证书」的签名。
#              这一道由 macOS 自带的 codesign 核对，和下载渠道无关
# 装到 ~/.local/bin/gh —— 只有这一个文件，删掉它就是卸载。
set -euo pipefail

PIN_VER="2.105.0"      # 2026-11-18 从官方 gh_2.105.0_checksums.txt 抄录
PIN_SHA_ARM64="2a5d03103ae444927383b93d9ffd65579a26e2eb900ce1cf11f55258d5319ded"
PIN_SHA_AMD64="23602cbccbbdc6285a8520745b714cb927ce2ca621779373c2a20cd8e33fda66"
TEAM_ID="VEKTX9H2N7"   # GitHub 在苹果登记的开发者团队编号
DEST_DIR="${HOME}/.local/bin"
REL="https://github.com/cli/cli/releases"

say() { printf '%s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }
fetch() { curl --proto '=https' --proto-redir '=https' -fsSL "$@"; }

# 代码签名要求：证书链到苹果根证书 + 是 Developer ID（对外分发用的开发者证书）+ 团队编号一致
req_for() {
  printf 'anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "%s"' "$1"
}
# signed_by <文件> <团队编号>：签名完好、且满足上面的要求才返回 0
signed_by() { codesign --verify --strict -R="$(req_for "$2")" "$1" >/dev/null 2>&1; }
signer_of() { codesign -dvv "$1" 2>&1 | grep -E '^(Authority=Developer ID|TeamIdentifier=)' | tr '\n' ' ' || true; }
latest_ver() {
  local loc
  loc="$(curl --proto '=https' -fsSI "${REL}/latest" | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}' | tail -1)"
  printf '%s' "${loc##*/tag/v}"
}

[ "$(uname -s)" = "Darwin" ] || die "这个脚本只给 macOS 用"
case "$(uname -m)" in
  arm64)  ARCH="arm64"; PIN_SHA="${PIN_SHA_ARM64}" ;;
  x86_64) ARCH="amd64"; PIN_SHA="${PIN_SHA_AMD64}" ;;
  *) die "不认识的芯片架构：$(uname -m)" ;;
esac
command -v codesign >/dev/null 2>&1 || die "找不到 codesign（macOS 自带的签名核对工具）"

MODE="${1:-}"

# ---------- 体检：不下载、不改东西 ----------
if [ "${MODE}" = "--check" ]; then
  say "SYSTEM macOS $(sw_vers -productVersion 2>/dev/null || echo '?') ${ARCH}"

  # 先证明「签名核对」这把尺子本身是准的：找一个本机已装的、由 Developer ID 签名的 App，
  # 用它真实的团队编号应当通过，换一个假编号应当不通过。
  PROBE=""; PROBE_TEAM=""
  for app in /Applications/Claude.app /Applications/*.app; do
    [ -d "${app}" ] || continue
    info="$(codesign -dvv "${app}" 2>&1 || true)"
    printf '%s\n' "${info}" | grep -q '^Authority=Developer ID Application' || continue
    t="$(printf '%s\n' "${info}" | sed -n 's/^TeamIdentifier=//p' | head -1)"
    if printf '%s' "${t}" | grep -Eq '^[A-Z0-9]{10}$'; then PROBE="${app}"; PROBE_TEAM="${t}"; break; fi
  done
  if [ -z "${PROBE}" ]; then
    say "SELFTEST_SKIPPED 没找到可用来对照的 App"
  elif signed_by "${PROBE}" "${PROBE_TEAM}" && ! signed_by "${PROBE}" "AAAAAAAAAA" && ! signed_by "${PROBE}" "${TEAM_ID}"; then
    say "SELFTEST_OK 对照 ${PROBE##*/}（${PROBE_TEAM}）：真编号通过，假编号和 GitHub 编号都不通过"
  else
    say "SELFTEST_FAIL 对照 ${PROBE##*/}（${PROBE_TEAM}）结果不符合预期"
  fi

  GH="$(command -v gh 2>/dev/null || true)"
  if [ -z "${GH}" ] && [ -x "${DEST_DIR}/gh" ]; then GH="${DEST_DIR}/gh"; fi
  if [ -z "${GH}" ]; then
    say "GH_NOT_INSTALLED"
  else
    say "GH_PATH ${GH}"
    say "GH_VERSION $("${GH}" --version 2>/dev/null | head -1)"
    if signed_by "${GH}" "${TEAM_ID}"; then say "GH_SIGNATURE_OK $(signer_of "${GH}")"
    else say "GH_SIGNATURE_NOT_GITHUB $(signer_of "${GH}")"; fi
    if "${GH}" auth status >/dev/null 2>&1; then
      say "GH_AUTH_OK $("${GH}" auth status 2>&1 | grep -E 'Logged in|Token scopes|protocol' | sed 's/^ *//' | tr '\n' ' ')"
    else
      say "GH_AUTH_MISSING 还没登录：${GH} auth login --hostname github.com --git-protocol https --web"
    fi
  fi

  v="$(latest_ver 2>/dev/null || true)"
  if [ -n "${v}" ]; then say "NET_OK 官方最新版 ${v}（脚本钉死的是 ${PIN_VER}）"; else say "NET_FAIL 连不上 github.com"; fi
  exit 0
fi

# ---------- 安装 ----------
if [ "${MODE}" = "--latest" ]; then
  VER="$(latest_ver)"
elif [ -z "${MODE}" ]; then
  VER="${PIN_VER}"
else
  die "不认识的参数：${MODE}（可用：--latest / --check）"
fi
printf '%s' "${VER}" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || die "没拿到合法的版本号：${VER}"

ZIP="gh_${VER}_macOS_${ARCH}.zip"
SUMS="gh_${VER}_checksums.txt"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/install-gh.XXXXXX")"
trap 'rm -rf "${WORK:?}"' EXIT
cd "${WORK}"

say "① 下载  ${REL}/download/v${VER}/${ZIP}"
fetch -o "${ZIP}"  "${REL}/download/v${VER}/${ZIP}"
fetch -o "${SUMS}" "${REL}/download/v${VER}/${SUMS}"

say "② 核对文件指纹（SHA-256）"
WANT="$(awk -v f="${ZIP}" '$2==f{print $1}' "${SUMS}")"
GOT="$(shasum -a 256 "${ZIP}" | awk '{print $1}')"
[ -n "${WANT}" ] || die "官方清单里没有 ${ZIP} 这一行。没有安装。"
[ "${WANT}" = "${GOT}" ] || die "和官方清单不一致（清单 ${WANT} / 实际 ${GOT}）。没有安装。"
if [ "${VER}" = "${PIN_VER}" ]; then
  [ "${GOT}" = "${PIN_SHA}" ] || die "和脚本里钉死的指纹不一致（钉死 ${PIN_SHA} / 实际 ${GOT}）。没有安装。"
  say "   ✓ ${GOT}（官方清单、脚本钉死值，两处都一致）"
else
  say "   ✓ ${GOT}（与官方清单一致；这个版本脚本里没有钉死值）"
fi

say "③ 核对苹果代码签名"
unzip -q "${ZIP}"
BIN="$(find "${WORK}" -type f -name gh -path '*/bin/*' | head -1)"
[ -n "${BIN}" ] || die "压缩包里没找到 bin/gh。没有安装。"
if ! signed_by "${BIN}" "${TEAM_ID}"; then
  say "   实际签名者：$(signer_of "${BIN}")"
  die "签名不是 GitHub（${TEAM_ID}）的开发者证书，或者文件被改动过。没有安装。"
fi
say "   ✓ $(signer_of "${BIN}")"

say "④ 安装到 ${DEST_DIR}/gh"
mkdir -p "${DEST_DIR}"
cp "${BIN}" "${DEST_DIR}/.gh.new"
chmod 755 "${DEST_DIR}/.gh.new"
mv -f "${DEST_DIR}/.gh.new" "${DEST_DIR}/gh"
signed_by "${DEST_DIR}/gh" "${TEAM_ID}" || die "装好的文件签名核对失败，请删除 ${DEST_DIR}/gh"
say "   ✓ $("${DEST_DIR}/gh" --version | head -1)"

say ""
say "装好了。下一步是登录（要你本人在浏览器里点授权，脚本不代劳）："
say "  ${DEST_DIR}/gh auth login --hostname github.com --git-protocol https --web"
