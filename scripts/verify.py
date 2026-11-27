"""Verify the released SDK layout and compile against both API surfaces."""

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from zipfile import ZipFile

from build import SDK_ANNOTATIONS, extract
from inputs import CACHE, LOCK_PATH, OUT, ROOT, fetch, read_lock

TESTS = ROOT / "tests"


def run(*command: Path | str, expected: int = 0) -> str:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != expected:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError(
            f"{command[0]} returned {result.returncode}; expected {expected}"
        )
    return result.stdout


def verify_layout(platform: Path, system: Path, version: str, revision: int) -> None:
    for name, count in (
        ("android.jar", 6440),
        ("core-for-system-modules.jar", 1764),
        ("optional/android.car.jar", 0),
        ("optional/org.apache.http.legacy.jar", 0),
        ("optional/android.test.base.jar", 0),
        ("optional/android.test.mock.jar", 0),
        ("optional/android.test.runner.jar", 0),
        ("uiautomator.jar", 0),
    ):
        with ZipFile(platform / name) as archive:
            assert archive.testzip() is None, name
            classes = [
                entry for entry in archive.namelist() if entry.endswith(".class")
            ]
            assert len(classes) == count if count else classes, name
            if name == "android.jar":
                assert "resources.arsc" in archive.namelist()
                assert set(SDK_ANNOTATIONS) <= set(classes)
            if name == "core-for-system-modules.jar":
                assert "java/lang/Enum$EnumDesc.class" in classes
                assert "java/lang/invoke/StringConcatFactory.class" in classes
    with ZipFile(system) as archive:
        assert archive.testzip() is None
        assert (
            len([name for name in archive.namelist() if name.endswith(".class")])
            == 8490
        )
        assert archive.read("META-INF/source-inputs.json") == LOCK_PATH.read_bytes()

    assert (platform / "source-inputs.json").read_bytes() == LOCK_PATH.read_bytes()
    properties = (platform / "source.properties").read_text().splitlines()
    assert f"AndroidVersion.ApiLevel={version}" in properties
    assert f"Pkg.Revision={revision}" in properties
    assert "AndroidVersion.IsBaseSdk=true" in properties
    assert "wear-sdk" not in (platform / "optional/optional.json").read_text()
    assert not (platform / "optional/wear-sdk.jar").exists()
    assert (platform / "data/annotations.zip").is_file()
    for directory in ("skins", "templates"):
        assert (platform / directory / "NOTICE.txt").is_file()
    history = ElementTree.parse(platform / "data/api-versions.xml")
    activity = history.find("class[@name='android/app/Activity']")
    assert activity is not None and activity.get("since") == "1"
    with ZipFile(platform / "android-stubs-src.jar") as source:
        assert {"android/app/Activity.java", "java/lang/Object.java"} <= set(
            source.namelist()
        )


def verify_compilation(
    platform: Path, system: Path, jdk: Path, build_tools: Path, work: Path
) -> None:
    javac = jdk / "bin/javac"
    public = platform / "android.jar"
    args = [
        "-source",
        "8",
        "-target",
        "8",
        "-Xlint:-options",
        "-proc:none",
        "-d",
        str(work),
    ]
    run(javac, *args, "-bootclasspath", public, TESTS / "PublicSdk.java")
    run(javac, *args, "-bootclasspath", system, TESTS / "SystemSdk.java")
    run(javac, *args, "-bootclasspath", public, TESTS / "SystemSdk.java", expected=1)

    aidl = build_tools / "aidl"
    out = work / "aidl"
    out.mkdir()
    run(
        aidl,
        "--lang=java",
        f"--preprocessed={platform / 'framework.aidl'}",
        "-I",
        TESTS / "aidl",
        "-o",
        out,
        TESTS / "aidl/probe/IDeviceProbe.aidl",
    )
    run(javac, *args, "-bootclasspath", public, out / "probe/IDeviceProbe.java")

    aapt2 = build_tools / "aapt2"
    apk = work / "fixture.apk"
    compiled = work / "resources.zip"
    run(aapt2, "compile", "--dir", TESTS / "resources", "-o", compiled)
    run(
        aapt2,
        "link",
        "-I",
        public,
        "--manifest",
        TESTS / "AndroidManifest.xml",
        "-o",
        apk,
        compiled,
    )
    assert "targetSdkVersion:'37'" in run(aapt2, "dump", "badging", apk)


def main() -> None:
    lock = read_lock()
    version = lock["version"]
    with TemporaryDirectory(dir=OUT, prefix="verify.") as directory:
        work = Path(directory)
        temp = work / "tmp"
        temp.mkdir()
        os.environ["TMPDIR"] = str(temp)
        os.environ["JAVA_TOOL_OPTIONS"] = (
            f'-XX:ActiveProcessorCount=8 -Djava.io.tmpdir="{temp}"'
        )
        for key in ("JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH"):
            os.environ.pop(key, None)
        jdk = work / "jdk"
        asm = "asm-9.9.jar"
        fetch(asm, lock["files"][asm])
        for name, spec in lock["files"].items():
            if name.startswith("aosstudio-jdk-"):
                fetch(name, spec)
                extract(CACHE / name, jdk)
            if spec["role"] == "verification":
                fetch(name, spec)
                run("unzip", "-q", CACHE / name, "-d", work)

        dist = OUT / "dist"
        system = dist / f"aosstudio-system-stubs-{version}.jar"
        run("unzip", "-q", dist / f"aosstudio-sdk-platform-{version}.zip", "-d", work)
        platform = work / f"android-{version}"
        verify_layout(platform, system, version, lock["revision"])
        run(
            jdk / "bin/java",
            "--class-path",
            CACHE / asm,
            TESTS / "DeclaredMethods.java",
            ROOT / "api" / version / "public-declared-methods.tsv",
            platform / "android.jar",
            system,
        )
        verify_compilation(platform, system, jdk, work / "build-tools", work)
    print(f"SDK Platform {version} and System stubs verified")


if __name__ == "__main__":
    main()
