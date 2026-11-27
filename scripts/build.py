"""Build an SDK platform and its accompanying System API stubs."""

from collections.abc import Iterable, Sequence
import os
from pathlib import Path
import re
from shutil import copyfile, copytree, rmtree
import subprocess
import tarfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from inputs import CACHE, LOCK_PATH, OUT, ROOT, Lock, digest, fetch_all, read_lock

WORK = OUT / "build"
SOURCES = WORK / "sources"
TOOLS = WORK / "tools"
JDK = TOOLS / "jdk"
TMP = WORK / "tmp"
ARCHIVE_DATE = "2026-06-05T00:00:00Z"
HOST_TOOLS = "aosstudio-aosp-host-tools"
TOOLS_SUFFIX = "-17.0.0_r1-linux-x86_64"
COMMANDS = {
    "metalava": "java",
    "sdkparcelables": "java",
    "aapt2": "generators",
    "merge_zips": "bootstrap",
    "soong_zip": "bootstrap",
}
ANNOTATIONS = WORK / "stub-annotations.jar"
SDK_ANNOTATIONS = (
    "android/annotation/FlaggedApi.class",
    "android/annotation/NonNull.class",
    "android/annotation/Nullable.class",
    "androidx/annotation/RecentlyNonNull.class",
    "androidx/annotation/RecentlyNullable.class",
)


def tool(command: str) -> Path:
    return TOOLS / f"{HOST_TOOLS}-{COMMANDS[command]}{TOOLS_SUFFIX}" / "bin" / command


def run(command: Path | str, *args: Path | str) -> None:
    env = os.environ | {
        "JAVA_HOME": str(JDK),
        "PATH": f"{JDK / 'bin'}:{os.environ['PATH']}",
        "JAVA_TOOL_OPTIONS": f'-XX:ActiveProcessorCount=8 -Djava.io.tmpdir="{TMP}"',
        "TMPDIR": str(TMP),
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    for name in ("JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH"):
        env.pop(name, None)
    subprocess.run([command, *args], cwd=WORK, env=env, check=True)


def extract(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source) as archive:
        archive.extractall(destination, filter="data")


def stage(lock: Lock) -> None:
    for name, spec in lock["files"].items():
        if spec["role"] == "source":
            directory = name.removesuffix(".tar.gz")
            if directory == "development-build":
                directory = "development/build"
            extract(CACHE / name, SOURCES / directory)
        elif spec["role"] == "tool" and name.startswith("aosstudio-jdk-"):
            extract(CACHE / name, JDK)
        elif spec["role"] == "tool":
            extract(CACHE / name, TOOLS)
    run(
        "patch",
        "--batch",
        "--forward",
        "--fuzz=0",
        "-p1",
        "-d",
        SOURCES / "development",
        "-i",
        ROOT / "patches/aosp-17-platform-without-wear.patch",
    )


def jar(output: Path, content: Path) -> None:
    run(
        JDK / "bin/jar",
        "--create",
        "--date",
        ARCHIVE_DATE,
        "--file",
        output,
        "-C",
        content,
        ".",
    )


def javac(
    name: str,
    files: Iterable[Path],
    classes: Path,
    classpath: Sequence[Path],
    *,
    source_level: str = "8",
) -> None:
    classes.mkdir(parents=True, exist_ok=True)
    argfile = WORK / f"{name}.args"
    argfile.write_text(
        "".join(f'"{source}"\n' for source in sorted(files)), encoding="utf-8"
    )
    run(
        JDK / "bin/javac",
        "-J-Xmx3g",
        "-source",
        source_level,
        "-target",
        source_level,
        "-Xlint:-options",
        "-encoding",
        "UTF-8",
        "-proc:none",
        "-classpath",
        os.pathsep.join(map(str, classpath)),
        "-d",
        classes,
        f"@{argfile}",
    )


def compile_signature(
    name: str, signatures: Sequence[Path], classpath: Sequence[Path], output: Path
) -> Path:
    source = WORK / "stubs" / name
    print(f"Building {name} stubs", flush=True)
    run(
        tool("metalava"),
        "--no-color",
        "--jdk-home",
        JDK,
        "--source-files",
        ",".join(map(str, signatures)),
        "--classpath",
        os.pathsep.join(map(str, classpath)),
        "--hide",
        "UnresolvedImport",
        "--exclude-annotation",
        "androidx.annotation.RestrictedForEnvironment",
        "--stubs",
        source,
    )
    classes = WORK / "classes" / name
    javac(name, source.rglob("*.java"), classes, classpath)
    jar(output, classes)
    return source


def public_signatures(api: Path) -> tuple[Path, Path]:
    public = api / "public.txt"
    methods: dict[str, list[str]] = {}
    for entry in (api / "public-declared-methods.tsv").read_text().splitlines():
        owner, _, _, declaration = entry.split("\t", 3)
        methods.setdefault(owner, []).append(f"    method {declaration};")

    class_header = re.compile(r"^  .*\b(?:class|interface|enum) ([\w.$]+)(?=[ <{]).*{$")
    packages: dict[str, list[str]] = {}
    package = ""
    for line in public.read_text().splitlines():
        if line.startswith("package "):
            package = line.removeprefix("package ").removesuffix(" {")
        elif match := class_header.fullmatch(line):
            owner = f"{package.replace('.', '/')}/{match[1].replace('.', '$')}"
            if owner in methods:
                packages.setdefault(package, []).extend(
                    [line, *methods.pop(owner), "  }", ""]
                )
    if methods:
        raise ValueError(f"No public API class for {', '.join(sorted(methods))}")

    supplement = WORK / "public-declared-methods.txt"
    text = ["// Signature format: 2.0", ""]
    for package, declarations in packages.items():
        text.extend([f"package {package} {{", "", *declarations, "}", ""])
    supplement.write_text("\n".join(text), encoding="utf-8")
    return public, supplement


def build_framework(lock: Lock) -> Path:
    framework = SOURCES / "framework-res"
    resources = WORK / "framework-res-compiled.zip"
    overlay = WORK / "remote-colors-compiled.zip"
    colors = WORK / "remote-theme-colors.zip"
    colors_apk = WORK / "remote-theme-colors.apk"
    remote = WORK / "remote-raw-res"
    apk = WORK / "framework-res.apk"
    aapt2 = tool("aapt2")
    flags = f"@{ROOT / 'configs/framework-feature-flags.txt'}"
    print("Compiling framework resources", flush=True)
    run(
        aapt2,
        "compile",
        "--legacy",
        "--feature-flags",
        flags,
        "--dir",
        framework / "res",
        "-o",
        resources,
    )
    run(
        aapt2,
        "compile",
        "--dir",
        framework / "remote_color_resources_res",
        "-o",
        colors,
    )
    run(
        aapt2,
        "link",
        "-o",
        colors_apk,
        "--manifest",
        framework / "RemoteThemeColorsAndroidManifest.xml",
        colors,
    )
    (remote / "raw").mkdir(parents=True, exist_ok=True)
    (remote / "values").mkdir(exist_ok=True)
    with ZipFile(colors_apk) as archive:
        (remote / "raw/remote_views_color_resources.arsc").write_bytes(
            archive.read("resources.arsc")
        )
    copyfile(
        framework / "remote_color_resources_res/symbols.xml",
        remote / "values/symbols.xml",
    )
    run(aapt2, "compile", "--dir", remote, "-o", overlay)
    api = lock["version"].partition(".")[0]
    run(
        aapt2,
        "link",
        "-o",
        apk,
        "--manifest",
        framework / "AndroidManifest.xml",
        "--private-symbols",
        "com.android.internal",
        "--no-auto-version",
        "--auto-add-overlay",
        "--enable-sparse-encoding",
        "--min-sdk-version",
        api,
        "--target-sdk-version",
        api,
        "--version-code",
        api,
        "--version-name",
        lock["android_version"],
        "--feature-flags",
        flags,
        resources,
        "-R",
        overlay,
    )
    return apk


def build_core(api: Path, public: Path, output: Path) -> None:
    print("Building core system-module stubs", flush=True)
    asm = sorted(CACHE.glob("asm*-9.9.jar"))
    exclude = {"JarJarMojo.java", "JarJarTask.java", "AntJarProcessor.java"}
    jarjar_source = [
        file
        for module in ("jarjar-java", "jarjar-android")
        for file in (SOURCES / module).rglob("*.java")
        if file.name not in exclude
    ]
    jarjar = WORK / "classes/jarjar"
    javac("jarjar", jarjar_source, jarjar, asm, source_level="17")
    copytree(SOURCES / "jarjar-resources", jarjar, dirs_exist_ok=True)
    lambda_root = SOURCES / "libcore-lambda"
    sources = [
        lambda_root / member
        for member in (ROOT / "configs/lambda-sources.txt").read_text().splitlines()
    ]
    lambda_classes = WORK / "classes/lambda"
    javac("lambda", sources, lambda_classes, [public])
    for source in sources:
        target = lambda_classes / source.relative_to(lambda_root / "java")
        copyfile(source, target)

    raw_lambda = WORK / "lambda-raw.jar"
    mapped_lambda = WORK / "lambda.jar"
    jar(raw_lambda, lambda_classes)
    run(
        JDK / "bin/java",
        "-cp",
        os.pathsep.join(map(str, [jarjar, *asm])),
        "com.tonicsystems.jarjar.Main",
        "process",
        CACHE / "libcore-lambda-rules.txt",
        raw_lambda,
        mapped_lambda,
    )
    core = WORK / "core-public.jar"
    with (
        ZipFile(public) as public_jar,
        ZipFile(core, "w", compression=ZIP_DEFLATED) as archive,
    ):
        for name in (api / "core-classes.txt").read_text().splitlines():
            info = ZipInfo(name, (2026, 6, 5, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, public_jar.read(name))
    run(tool("merge_zips"), "-j", "-D", output, core, mapped_lambda)


def assemble_public_jar(public: Path, resources: Path, output: Path) -> None:
    annotations = WORK / "exported-annotations.jar"
    with (
        ZipFile(ANNOTATIONS) as inputs,
        ZipFile(annotations, "w", compression=ZIP_DEFLATED) as archive,
    ):
        for name in SDK_ANNOTATIONS:
            archive.writestr(inputs.getinfo(name), inputs.read(name))
    run(tool("merge_zips"), "-j", "-D", output, public, resources, annotations)


def optional_libraries(platform: Path, public: Path) -> None:
    optional = platform / "optional"
    names = {"apache-http": "org.apache.http.legacy", "car": "android.car"}
    for source, name in names.items():
        signature = SOURCES / f"{source}-api/current.txt"
        compile_signature(
            name, [signature], [public, ANNOTATIONS], optional / f"{name}.jar"
        )

    test_jars: list[Path] = []
    for package in ("base", "mock", "runner"):
        name = f"android.test.{package}"
        signature = SOURCES / f"android-test-{package}-api/current.txt"
        library = optional / f"{name}.jar"
        compile_signature(name, [signature], [public, ANNOTATIONS, *test_jars], library)
        test_jars.append(library)

    compile_signature(
        "uiautomator",
        [SOURCES / "uiautomator-api/current.txt"],
        [public, ANNOTATIONS, *test_jars],
        platform / "uiautomator.jar",
    )


def platform_data(lock: Lock, platform: Path, api: Path) -> None:
    data = platform / "data"
    dev = SOURCES / "development-sdk"
    data.mkdir()
    optional = platform / "optional"
    optional.mkdir()
    metadata = api / "metadata"
    for path in sorted(metadata.iterdir()):
        folder = platform if path.name in {"build.prop", "framework.aidl"} else data
        copyfile(path, folder / path.name)
    copyfile(api / "api-versions.xml", data / "api-versions.xml")
    copyfile(SOURCES / "development/build/optional.json", optional / "optional.json")
    copyfile(dev / "sdk.properties", platform / "sdk.properties")
    copytree(SOURCES / "framework-res/res", data / "res")
    copytree(SOURCES / "binder-headers", optional / "libbinder_ndk_cpp")
    copytree(SOURCES / "development-templates", platform / "templates")
    copytree(
        SOURCES / "development-skins",
        platform / "skins",
        ignore=lambda _root, names: {"NOTICE.txt"} & set(names),
    )
    properties = (dev / "platform_source.prop_template").read_text()
    for key, value in {
        "PLATFORM_VERSION": lock["android_version"],
        "PLATFORM_SDK_VERSION": lock["version"],
        "PLATFORM_SDK_EXTENSION_VERSION": lock["extension_level"],
        "PLATFORM_IS_BASE_SDK": "true",
        "PLATFORM_VERSION_CODENAME": "",
        "PLATFORM_PREVIEW_SDK_VERSION": "0",
        "BETA_SDK_VERSION": "",
    }.items():
        properties = properties.replace("${" + key + "}", value)
    properties = re.sub(
        r"(?m)^Pkg\.Revision=\d+$", f"Pkg.Revision={lock['revision']}", properties
    )
    (platform / "source.properties").write_text(properties)
    for folder in (
        platform,
        data,
        optional,
        platform / "skins",
        platform / "templates",
    ):
        copyfile(dev / "sdk_files_NOTICE.txt", folder / "NOTICE.txt")
    licenses = platform / "licenses"
    licenses.mkdir()
    copyfile(ROOT / "LICENSE", licenses / "AOSStudio-LICENSE")
    copyfile(SOURCES / "framework-res/NOTICE", licenses / "framework-res-NOTICE")
    copyfile(CACHE / "apache-http-NOTICE", licenses / "apache-http-NOTICE")
    copyfile(JDK / "legal/java.base/LICENSE", licenses / "libcore-lambda-LICENSE")
    copyfile(LOCK_PATH, platform / "source-inputs.json")


def build(lock: Lock) -> None:
    version = lock["version"]
    api = ROOT / "api" / version
    platform = WORK / f"android-{version}"
    platform.mkdir()
    platform_data(lock, platform, api)

    annotation_classes = WORK / "classes/annotations"
    javac(
        "annotations",
        (SOURCES / "metalava-annotations").rglob("*.java"),
        annotation_classes,
        [],
    )
    jar(ANNOTATIONS, annotation_classes)
    public = WORK / "public-classes.jar"
    signatures = public_signatures(api)
    public_src = compile_signature("public", signatures, [ANNOTATIONS], public)
    jar(platform / "android-stubs-src.jar", public_src)
    resources = build_framework(lock)
    assemble_public_jar(public, resources, platform / "android.jar")
    build_core(api, public, platform / "core-for-system-modules.jar")
    optional_libraries(platform, public)

    system = WORK / f"aosstudio-system-stubs-{version}.jar"
    compile_signature(
        "system", [*signatures, api / "system.txt"], [ANNOTATIONS], system
    )
    notices = WORK / "system-notices/META-INF"
    notices.mkdir(parents=True)
    copyfile(ROOT / "LICENSE", notices / "LICENSE")
    copyfile(LOCK_PATH, notices / "source-inputs.json")
    run(
        JDK / "bin/jar",
        "--update",
        "--date",
        ARCHIVE_DATE,
        "--file",
        system,
        "-C",
        notices.parent,
        "META-INF",
    )

    package = WORK / f"aosstudio-sdk-platform-{version}.zip"
    run(tool("soong_zip"), "-o", package, "-C", WORK, "-D", platform)
    dist = OUT / "dist"
    dist.mkdir(exist_ok=True)
    for artifact in (package, system):
        artifact.replace(dist / artifact.name)
        print(dist / artifact.name)


def main() -> None:
    lock = read_lock()
    for name, expected in lock["snapshots"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"SHA-256 mismatch for {name}")
    fetch_all(lock)
    if WORK.exists():
        rmtree(WORK)
    WORK.mkdir()
    TMP.mkdir()
    stage(lock)
    build(lock)


if __name__ == "__main__":
    main()
