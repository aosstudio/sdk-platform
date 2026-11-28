import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipFile;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

class DeclaredMethods {
    private record Method(String name, String descriptor) {}

    public static void main(String[] args) throws Exception {
        // JNI Zero looks for methods declared on the requested owner.
        Map<String, Set<Method>> expected = Map.of(
                "java/util/UUID", Set.of(new Method("toString", "()Ljava/lang/String;"),
                        new Method("equals", "(Ljava/lang/Object;)Z"), new Method("hashCode", "()I")),
                "android/animation/AnimatorSet", Set.of(new Method("cancel", "()V")));

        for (String path : args) {
            try (ZipFile jar = new ZipFile(path)) {
                for (var entry : expected.entrySet()) {
                    String owner = entry.getKey();
                    var member = jar.getEntry(owner + ".class");
                    if (member == null) {
                        throw new AssertionError(jar.getName() + " is missing " + owner);
                    }
                    var missing = new HashSet<>(entry.getValue());
                    try (var stream = jar.getInputStream(member)) {
                        new ClassReader(stream).accept(new ClassVisitor(Opcodes.ASM9) {
                            @Override
                            public MethodVisitor visitMethod(int access, String name,
                                    String descriptor, String signature, String[] exceptions) {
                                if ((access & (Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC))
                                        == Opcodes.ACC_PUBLIC) {
                                    missing.remove(new Method(name, descriptor));
                                }
                                return null;
                            }
                        }, ClassReader.SKIP_CODE | ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
                    }
                    if (!missing.isEmpty()) {
                        throw new AssertionError(jar.getName() + " lacks declarations in "
                                + owner + ": " + missing);
                    }
                }
            }
        }
    }
}
