"""Large-PR review strategy.

When a pull request touches more than ``LARGE_PR_DIFF_LINES`` changed lines, a
single prompt cannot hold the whole diff without losing the tail of it. In that
case the service switches to a per-file strategy:

* rank every changed file by importance (interface definitions first, then
  configuration/dependency changes, then ordinary business code);
* drop files that never deserve a review slot (tests, docs, lock files,
  generated or vendored code, assets, minified bundles);
* keep at most ``LARGE_PR_MAX_FILES`` files and review them one at a time, so a
  single request never carries the whole diff.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .diff_parser import ChangedFile, is_test_file


DEFAULT_LARGE_PR_DIFF_LINES = 500
DEFAULT_LARGE_PR_MAX_FILES = 5
DEFAULT_LARGE_PR_FILE_MAX_CHARS = 60000
DEFAULT_LARGE_PR_CONCURRENCY = 2

TIER_INTERFACE = 3
TIER_CONFIG = 2
TIER_BUSINESS = 1
TIER_EXCLUDED = 0

TIER_LABELS = {
    TIER_INTERFACE: "接口定义",
    TIER_CONFIG: "配置/依赖",
    TIER_BUSINESS: "业务代码",
}

REASON_TEST = "测试文件"
REASON_DOC = "文档"
REASON_LOCK = "依赖锁文件"
REASON_GENERATED = "生成代码或第三方代码"
REASON_ASSET = "资源文件"
REASON_I18N = "多语言文件"
REASON_MINIFIED = "压缩产物"
REASON_EMPTY = "没有文本 diff"
REASON_LIMIT = "超出本次检视文件数上限"

INTERFACE_SUFFIXES = {
    ".h", ".hh", ".hpp", ".hxx", ".proto", ".thrift", ".idl", ".capnp", ".fbs", ".wsdl",
}
INTERFACE_DIR_PARTS = {
    "api", "apis", "interface", "interfaces", "routes", "router", "routers",
    "controller", "controllers", "endpoint", "endpoints", "contract", "contracts",
    "openapi", "swagger",
}
INTERFACE_STEM_RE = re.compile(r"(^|[._-])(api|openapi|swagger|proto|contract|dto|interface)([._-]|$)", re.I)
INTERFACE_TYPE_RE = re.compile(r"^I[A-Z]\w+\.(cs|java)$")

CONFIG_SUFFIXES = {
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".env",
    ".tf", ".tfvars", ".gradle", ".plist",
}
CONFIG_NAMES = {
    "dockerfile", "makefile", "cmakelists.txt", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "pyproject.toml",
    "setup.py", "setup.cfg", "requirements.txt", "requirements-dev.txt", "values.yaml",
    "values.yml", "chart.yaml", "package.json", "tsconfig.json", "jenkinsfile",
    "procfile", "nginx.conf", "cmakecache.txt",
}
CONFIG_DIR_PARTS = {
    "config", "configs", "conf", "settings", "deploy", "deploys", "deployment",
    "deployments", "helm", "k8s", "kubernetes", "charts", "manifests", "workflows",
    ".github", ".gitlab", ".circleci", "ci",
}

DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc", ".mdx"}
LOCK_NAMES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "pipfile.lock", "composer.lock", "cargo.lock", "go.sum",
    "gemfile.lock", "packages.lock.json", "flake.lock",
}
LOCK_SUFFIXES = {".lock", ".sum"}
GENERATED_DIR_PARTS = {
    "vendor", "node_modules", "dist", "build", "out", "target", "generated",
    "__generated__", ".venv", "venv", "site-packages", ".next", ".nuxt", ".cache",
    "coverage", "third_party", "thirdparty", "3rdparty", "external", "deps",
}
ASSET_SUFFIXES = {
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp", ".woff",
    ".woff2", ".ttf", ".eot", ".otf", ".pdf", ".zip", ".gz", ".tar", ".jar", ".so",
    ".dll", ".dylib", ".exe", ".bin", ".wav", ".mp4", ".po", ".pot", ".mo",
}
I18N_PARTS = {"locale", "locales", "i18n", "lang", "langs", "translations"}
SNAPSHOT_PARTS = {"__snapshots__", "snapshots"}
GENERATED_NAME_RE = re.compile(r"(\.pb\.go|\.g\.dart|_pb2\.py|_pb2_grpc\.py|\.designer\.cs|\.generated\.|\.min\.(js|css)$)", re.I)


@dataclass(slots=True)
class ReviewPlan:
    """How the changed files should be reviewed."""

    large: bool
    changed_lines: int
    threshold: int
    file_limit: int
    selected: list[ChangedFile]
    selected_tiers: dict[str, str]
    skipped: list[tuple[str, str]]

    @property
    def selected_paths(self) -> list[str]:
        return [file.new_path for file in self.selected]


def large_pr_threshold() -> int:
    return _env_int("LARGE_PR_DIFF_LINES", DEFAULT_LARGE_PR_DIFF_LINES, minimum=1, maximum=1_000_000)


def large_pr_file_limit() -> int:
    return _env_int("LARGE_PR_MAX_FILES", DEFAULT_LARGE_PR_MAX_FILES, minimum=1, maximum=100)


def per_file_max_chars() -> int:
    return _env_int("LARGE_PR_FILE_MAX_CHARS", DEFAULT_LARGE_PR_FILE_MAX_CHARS, minimum=1, maximum=10_000_000)


def large_pr_concurrency() -> int:
    return _env_int("LARGE_PR_CONCURRENCY", DEFAULT_LARGE_PR_CONCURRENCY, minimum=1, maximum=16)


def changed_line_count(file: ChangedFile) -> int:
    return sum(1 for hunk in file.hunks for line in hunk.lines if line.kind in {"add", "delete"})


def count_changed_lines(files: list[ChangedFile]) -> int:
    return sum(changed_line_count(file) for file in files)


def build_review_plan(files: list[ChangedFile]) -> ReviewPlan:
    """Decide whether this PR is large and, if so, which files to review."""

    threshold = large_pr_threshold()
    limit = large_pr_file_limit()
    total = count_changed_lines(files)
    reviewable = [file for file in files if file.hunks]

    if total <= threshold:
        tiers = {file.new_path: TIER_LABELS[TIER_BUSINESS] for file in reviewable}
        skipped = [(file.new_path, REASON_EMPTY) for file in files if not file.hunks]
        return ReviewPlan(
            large=False,
            changed_lines=total,
            threshold=threshold,
            file_limit=limit,
            selected=reviewable,
            selected_tiers=tiers,
            skipped=skipped,
        )

    ranked: list[tuple[int, int, str, ChangedFile]] = []
    tiers: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    for file in files:
        tier, reason = _classify(file)
        if not file.hunks:
            skipped.append((file.new_path, REASON_EMPTY))
            continue
        if tier == TIER_EXCLUDED:
            skipped.append((file.new_path, reason))
            continue
        ranked.append((tier, changed_line_count(file), file.new_path, file))

    # Importance tier dominates; within a tier the most-changed file goes first.
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))

    selected_entries = ranked[:limit]
    for _, _, _, file in ranked[limit:]:
        skipped.append((file.new_path, REASON_LIMIT))

    selected = [entry[3] for entry in selected_entries]
    for tier, _, path, _ in selected_entries:
        tiers[path] = TIER_LABELS[tier]

    return ReviewPlan(
        large=True,
        changed_lines=total,
        threshold=threshold,
        file_limit=limit,
        selected=selected,
        selected_tiers=tiers,
        skipped=skipped,
    )


def skip_summary(plan: ReviewPlan, max_names: int = 6) -> str:
    """Human readable summary of what the large-PR strategy dropped."""

    if not plan.skipped:
        return ""
    counts: dict[str, int] = {}
    for _, reason in plan.skipped:
        counts[reason] = counts.get(reason, 0) + 1
    parts = [f"{reason} {count} 个" for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    names = [path for path, _ in plan.skipped[:max_names]]
    listed = "、".join(names)
    if len(plan.skipped) > max_names:
        listed = f"{listed} 等"
    return f"；未检视 {len(plan.skipped)} 个文件（{'，'.join(parts)}）: {listed}"


def _classify(file: ChangedFile) -> tuple[int, str]:
    path = file.new_path or file.old_path
    normalized = path.replace("\\", "/").strip()
    parts = [part for part in normalized.lower().split("/") if part]
    name = parts[-1] if parts else normalized.lower()
    directories = parts[:-1]
    suffix = _suffix(name)

    if not file.hunks:
        return TIER_EXCLUDED, REASON_EMPTY

    if is_test_file(path):
        return TIER_EXCLUDED, REASON_TEST
    if GENERATED_NAME_RE.search(name):
        return TIER_EXCLUDED, REASON_GENERATED
    if suffix in DOC_SUFFIXES:
        return TIER_EXCLUDED, REASON_DOC
    if name in LOCK_NAMES or suffix in LOCK_SUFFIXES:
        return TIER_EXCLUDED, REASON_LOCK
    if any(part in GENERATED_DIR_PARTS for part in directories):
        return TIER_EXCLUDED, REASON_GENERATED
    if any(part in I18N_PARTS for part in directories) or suffix in {".po", ".pot", ".mo"}:
        return TIER_EXCLUDED, REASON_I18N
    if any(part in SNAPSHOT_PARTS for part in directories) or suffix == ".snap":
        return TIER_EXCLUDED, REASON_TEST
    if suffix in ASSET_SUFFIXES:
        return TIER_EXCLUDED, REASON_ASSET

    if _is_interface(name, suffix, directories):
        return TIER_INTERFACE, ""
    if _is_config(name, suffix, directories):
        return TIER_CONFIG, ""
    return TIER_BUSINESS, ""


def _is_interface(name: str, suffix: str, directories: list[str]) -> bool:
    if suffix in INTERFACE_SUFFIXES:
        return True
    if name.endswith(".d.ts"):
        return True
    if INTERFACE_TYPE_RE.match(name):
        return True
    if any(part in INTERFACE_DIR_PARTS for part in directories):
        return True
    return bool(INTERFACE_STEM_RE.search(name))


def _is_config(name: str, suffix: str, directories: list[str]) -> bool:
    if suffix in CONFIG_SUFFIXES:
        return True
    if name in CONFIG_NAMES or name.startswith(".env"):
        return True
    if name.startswith("docker-compose") or name.startswith("requirements"):
        return True
    return any(part in CONFIG_DIR_PARTS for part in directories)


def _suffix(name: str) -> str:
    if "." not in name:
        return ""
    return name[name.rindex("."):]


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(minimum, min(value, maximum))
