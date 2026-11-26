# API and framework data

The 37.0 public and System signature snapshots in `api/37.0` are source-derived API declarations from AOSStudio's AOSP export. The public aggregate combines the framework, libcore, and module declarations with the selected `cp2a` flag values. The System snapshot is the delta layered on that public surface. Per-scope receipts record the digests and the comparison with the fixed 37.0 reference. Both sets of class files and the public Java stubs are generated during the build.

The platform metadata snapshots preserve the source-built platform's AIDL declarations and tooling data. The history covers the available reviewed AOSP inputs, with partial coverage for older modules and API 36.1. Framework resources, the Binder headers, SDK templates, skins, and optional-library declarations come from the fixed AOSP source archives in the build lock.

The framework flag list records resolved `cp2a` values used when compiling and linking upstream resources. The lambda source list follows the pinned libcore `openjdk_lambda_stub_files` declaration. The core system-module JAR combines the exported public core members with those source-built lambda classes. [Libcore source rules](https://android.googlesource.com/platform/libcore/+/4ebfb391ee0109d16abe6e2bc965724b940928c5/openjdk_java_files.bp)
