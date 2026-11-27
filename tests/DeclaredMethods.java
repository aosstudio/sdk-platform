import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
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
        Map<String, Set<Method>> expected = new HashMap<>();
        for (String row : Files.readAllLines(Path.of(args[0]))) {
            String[] fields = row.split("\t", 4);
            expected.computeIfAbsent(fields[0], owner -> new HashSet<>())
                    .add(new Method(fields[1], fields[2]));
        }

        for (int index = 1; index < args.length; index++) {
            try (ZipFile jar = new ZipFile(args[index])) {
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
