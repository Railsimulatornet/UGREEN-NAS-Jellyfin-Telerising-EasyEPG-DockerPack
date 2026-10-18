#!/usr/bin/env python3
"""Build and verify commit-named DockerPack ZIPs using committed package files."""

import argparse
import configparser
import hashlib
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile


PACKS = {
    "jellyfin": {
        "source": "v1",
        "project": "JellyfinServer",
        "name": "Jellyfin_Telerising_EasyEPG_DockerPack",
        "manual": "JellyfinServer_Handbuch_{language}_v1_jellyfin.pdf",
        "compose": ("docker-composeDXP.yaml", "docker-composeDH.yaml"),
        "config": "Jellyfin/config",
    },
    "emby": {
        "source": "",
        "project": "embyserver",
        "name": "Emby_Telerising_EasyEPG_DockerPack",
        "manual": "EmbyServer_Handbuch_{language}_v1.pdf",
        "compose": ("docker-compose.yaml",),
        "config": "emby/config",
    },
}

# These filters are supplied by SWAG/Fail2Ban. Project overrides are checked too.
# jellyfin-auth is deliberately excluded: this pack supplies its own filter.
IMAGE_FILTERS = {
    "nginx-http-auth", "nginx-badbots", "nginx-botsearch", "nginx-deny",
    "nginx-unauthorized", "recidive",
}


def git(repository, *args):
    return subprocess.check_output(["git", *args], cwd=repository)


def package_files(repository, commit, pack, language):
    source = PurePosixPath(pack["source"], language)
    entries = git(repository, "ls-tree", "-rz", "--full-tree", commit, "--", str(source))
    files = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Package sources must be regular tracked files")
        name = PurePosixPath(path.decode("utf-8")).relative_to(source).as_posix()
        files[name] = (int(mode, 8), git(repository, "cat-file", "blob", blob))

    project = pack["project"]
    required = {f"{project}/.env", f"{project}/linuxserver_swag/config/fail2ban/jail.local"}
    required.update(f"{project}/{name}" for name in pack["compose"])
    required.add(pack["manual"].format(language=language.upper()))
    missing = required - files.keys()
    if missing:
        raise ValueError(f"Missing tracked package files: {', '.join(sorted(missing))}")
    return files


def package_directories(files, pack):
    directories = set()
    for name in files:
        directories.update(str(parent) + "/" for parent in PurePosixPath(name).parents if str(parent) != ".")
    # Git cannot track empty directories; retain these existing archive paths.
    for relative in (pack["config"], "takealug_new-easyepg/easyepg", "takealug_new-easyepg/easyepg_xml", "telerising"):
        path = PurePosixPath(pack["project"], relative)
        directories.add(str(path) + "/")
        directories.update(str(parent) + "/" for parent in path.parents if str(parent) != ".")
    return directories


def build_archive(path, files, directories):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(files.keys() | directories):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            if name in directories:
                info.external_attr = (0o40755 << 16) | 0x10
                content = b""
            else:
                mode, content = files[name]
                info.external_attr = mode << 16
            archive.writestr(info, content)


def validate_filters(project):
    fail2ban = project / "linuxserver_swag/config/fail2ban"
    jails = configparser.ConfigParser(interpolation=None)
    with (fail2ban / "jail.local").open(encoding="utf-8-sig") as source:
        jails.read_file(source)
    for jail in jails.sections():
        if not jails.getboolean(jail, "enabled", fallback=False):
            continue
        name = jails.get(jail, "filter", fallback=jail).split("[", 1)[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(f"Invalid filter name in active jail {jail}")
        path = fail2ban / "filter.d" / f"{name}.conf"
        if name in IMAGE_FILTERS and not path.exists():
            continue
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Active jail {jail} is missing its packaged filter: {name}.conf")
        definition = configparser.ConfigParser(interpolation=None)
        with path.open(encoding="utf-8-sig") as source:
            definition.read_file(source)
        if not definition.get("Definition", "failregex", fallback="").strip():
            raise ValueError(f"Packaged filter {name}.conf has no failregex")


def validate_archive(path, files, directories, pack, destination):
    with zipfile.ZipFile(path) as archive:
        names = set()
        for entry in archive.infolist():
            name = entry.filename
            if name in names or "\\" in name or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/")):
                raise ValueError(f"Unsafe or duplicate archive path: {name}")
            names.add(name)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise ValueError(f"Archive contains a non-regular member: {name}")
            if entry.is_dir():
                if name not in directories:
                    raise ValueError(f"Unexpected archive directory: {name}")
            elif name not in files or entry.file_size != len(files[name][1]):
                raise ValueError(f"Unexpected archive file or size: {name}")
        if archive.testzip() is not None:
            raise ValueError("Archive CRC verification failed")
        destination.mkdir(parents=True, exist_ok=False)
        archive.extractall(destination)
        validate_filters(destination / pack["project"])
        missing = (files.keys() | directories) - names
        if missing:
            raise ValueError(f"Archive is incomplete: {', '.join(sorted(missing))}")
        for name, (_, content) in files.items():
            if (destination / name).read_bytes() != content:
                raise ValueError(f"Archive differs from committed package source: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", choices=PACKS)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--check-archive", type=Path, help="Check an existing ZIP against the current commit")
    parser.add_argument("--language", choices=("de", "en"), help="Language of --check-archive")
    args = parser.parse_args()
    if bool(args.check_archive) != bool(args.language):
        parser.error("--check-archive and --language must be used together")
    repository = Path(__file__).resolve().parents[2]
    pack = PACKS[args.pack]
    try:
        commit = git(repository, "rev-parse", "HEAD").decode("ascii").strip()
        if args.check_archive:
            files = package_files(repository, commit, pack, args.language)
            directories = package_directories(files, pack)
            with tempfile.TemporaryDirectory(prefix="dockerpack-check-") as temporary:
                validate_archive(args.check_archive, files, directories, pack, Path(temporary) / "unpacked")
            print(f"Verified {args.check_archive.name} against {commit}")
            return
        if args.output.exists() and any(args.output.iterdir()):
            raise ValueError("Output directory must be empty")
        args.output.mkdir(parents=True, exist_ok=True)
        checksums = []
        for language in ("de", "en"):
            files = package_files(repository, commit, pack, language)
            directories = package_directories(files, pack)
            path = args.output / f"{pack['name']}_{commit}_{language}.zip"
            build_archive(path, files, directories)
            validate_archive(path, files, directories, pack, args.output / "unpacked" / language)
            checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
            print(f"Built and verified {path.name}")
        (args.output / "SHA256SUMS").write_text("".join(checksums), encoding="ascii")
    except (OSError, ValueError, configparser.Error, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Package validation failed: {error}\n")


if __name__ == "__main__":
    main()
