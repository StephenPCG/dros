from __future__ import annotations

from pydantic import ValidationError

from dros.config_objects import ConfigObject, SystemMirrorConfig
from dros.plugins.base import BootstrapContext, DrosPlugin, UpdateContext

PACKAGES = frozenset({"ca-certificates", "curl"})
MANAGED_FILES = frozenset(
    {
        "/etc/apt/sources.list",
        "/etc/apt/sources.list.d/debian.sources",
        "/etc/apt/sources.list.d/docker-ce.list",
        "/etc/apt/sources.list.d/tailscale.list",
        "/usr/share/keyrings/tailscale-archive-keyring.gpg",
    }
)


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="system.mirror",
        config_kinds=frozenset({"SystemMirrorConfig"}),
        packages=PACKAGES,
        managed_files=MANAGED_FILES,
        bootstrap_hook=bootstrap,
        validation_hook=validate,
        update_hook=update,
    )


def bootstrap(context: BootstrapContext) -> None:
    mirror = context.configs.resolve("SystemMirrorConfig", SystemMirrorConfig)
    codename = _debian_codename(context)
    arch = _debian_architecture(context)

    context.executor.write_file(
        "/etc/apt/sources.list",
        _debian_sources(mirror.apt_mirror, codename),
    )
    context.executor.delete_file("/etc/apt/sources.list.d/debian.sources")
    context.executor.install_missing_packages(PACKAGES)
    context.executor.ensure_dir("/etc/apt/keyrings")
    if not context.executor.exists("/etc/apt/keyrings/docker.asc"):
        context.executor.run(
            [
                "curl",
                "-fsSL",
                f"{mirror.docker_apt_mirror.rstrip('/')}/linux/debian/gpg",
                "-o",
                "/etc/apt/keyrings/docker.asc",
            ],
            real_only=True,
        )
        context.executor.run(["chmod", "a+r", "/etc/apt/keyrings/docker.asc"], real_only=True)
    context.executor.ensure_dir("/usr/share/keyrings")
    if not context.executor.exists("/usr/share/keyrings/tailscale-archive-keyring.gpg"):
        context.executor.run(
            [
                "curl",
                "-fsSL",
                f"{mirror.tailscale_apt_mirror.rstrip('/')}/debian/{codename}.noarmor.gpg",
                "-o",
                "/usr/share/keyrings/tailscale-archive-keyring.gpg",
            ],
            real_only=True,
        )
        context.executor.run(
            ["chmod", "a+r", "/usr/share/keyrings/tailscale-archive-keyring.gpg"],
            real_only=True,
        )
    docker_source_changed = context.executor.write_file(
        "/etc/apt/sources.list.d/docker-ce.list",
        _docker_source(mirror.docker_apt_mirror, codename, arch),
    )
    tailscale_source_changed = context.executor.write_file(
        "/etc/apt/sources.list.d/tailscale.list",
        _tailscale_source(mirror.tailscale_apt_mirror, codename),
    )
    if docker_source_changed or tailscale_source_changed:
        context.executor.mark_package_indexes_stale()


def validate(context: UpdateContext, objects: list[ConfigObject]) -> list[str]:
    errors: list[str] = []
    for obj in objects:
        if obj.kind != "SystemMirrorConfig":
            continue
        try:
            context.configs.resolve_object(obj, SystemMirrorConfig)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                errors.append(f"{obj.kind}/{obj.name}: spec.{location}: {error['msg']}")
    return errors


def update(context: UpdateContext, _objects: object) -> None:
    bootstrap(context)


def _debian_sources(apt_mirror: str, codename: str) -> str:
    mirror = apt_mirror.rstrip("/")
    security = _debian_security_mirror(mirror)
    components = "main contrib non-free non-free-firmware"
    return "\n".join(
        [
            f"deb {mirror} {codename} {components}",
            f"deb {mirror} {codename}-updates {components}",
            f"deb {security} {codename}-security {components}",
            "",
        ]
    )


def _docker_source(docker_apt_mirror: str, codename: str, arch: str) -> str:
    mirror = docker_apt_mirror.rstrip("/")
    return (
        f"deb [arch={arch} signed-by=/etc/apt/keyrings/docker.asc] "
        f"{mirror}/linux/debian {codename} stable\n"
    )


def _tailscale_source(tailscale_apt_mirror: str, codename: str) -> str:
    mirror = tailscale_apt_mirror.rstrip("/")
    return (
        "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] "
        f"{mirror}/debian {codename} main\n"
    )


def _debian_security_mirror(apt_mirror: str) -> str:
    if apt_mirror.endswith("/debian"):
        return f"{apt_mirror.removesuffix('/debian')}/debian-security"
    return f"{apt_mirror}-security"


def _debian_codename(context: BootstrapContext) -> str:
    os_release = context.executor.read_text("/etc/os-release") or ""
    for line in os_release.splitlines():
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip().strip('"') or "trixie"
    return "trixie"


def _debian_architecture(context: BootstrapContext) -> str:
    arch = context.executor.output(["dpkg", "--print-architecture"], default="amd64")
    return arch or "amd64"
