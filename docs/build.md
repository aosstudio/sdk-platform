# Build SDK Platform

Build on Linux x86-64 with Python 3.14, GNU Make, curl, flock, patch, and unzip. The build uses the locked AOSStudio JDK and AOSP Host Tools Releases. The first platform follows the Android 17.0.0 r1 source manifest and the `cp2a` release configuration. [Input lock](../locks/inputs.json)

```sh
make build
make verify
```

`make fetch` prepares the digest-checked archive cache. Source archives, extracted tools, intermediate files, and packages are held under the disk-backed `out/` directory.

Metalava's API signature files omit some overrides whose method signatures match an inherited public method. The build reads declared methods from the locked AOSP Java stub sources and restores matching overrides to both API surfaces. The API signatures still select the classes and members exposed by the build. [AOSP stub source archive](https://android.googlesource.com/platform/prebuilts/fullsdk/platforms/+/39cfe045632329306ca749cdc0ad1d4670d3303c/android-37.0/android-stubs-src.jar)

| Output | Use |
| --- | --- |
| `out/dist/aosstudio-sdk-platform-37.0.zip` | Install its `android-37.0/` directory under `<sdk>/platforms/`. It contains the public stub JAR and source stubs, framework resources, AIDL declarations, API metadata, and optional libraries. |
| `out/dist/aosstudio-system-stubs-37.0.jar` | Add this compile-only JAR to projects that require System APIs. It includes the public and System API surfaces and is used by the WebView build. |

Both packages are architecture-independent. Verification compiles Java against the two classpaths, tests a Binder interface importing the SDK's Bluetooth API, and links resources against the SDK platform's framework resource table.

The Wear vendor stub and its optional-library index entry are omitted. The five remaining entries follow the fixed AOSP package definition. [AOSP packaging definition](https://android.googlesource.com/platform/development/+/4dafd114fab3c3d9543a5aff0ad097f479915176/build/Android.bp)
