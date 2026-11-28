import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.Trees;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.zip.ZipFile;
import javax.lang.model.element.ExecutableElement;
import javax.lang.model.element.Modifier;
import javax.lang.model.element.TypeElement;
import javax.lang.model.type.ArrayType;
import javax.lang.model.type.DeclaredType;
import javax.lang.model.type.TypeMirror;
import javax.lang.model.util.Elements;
import javax.lang.model.util.Types;
import javax.tools.JavaFileObject;
import javax.tools.ToolProvider;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

class DeclaredOverrides {
    private record Method(String name, String descriptor) {}

    private record ApiClass(List<String> parents, Map<Method, Integer> methods) {}

    public static void main(String[] args) throws IOException {
        Map<String, ApiClass> api = readApi(Path.of(args[0]));
        var restored = new TreeMap<String, String>();
        var compiler = ToolProvider.getSystemJavaCompiler();
        // The snapshot has documentation annotations and release-specific APIs outside our scope.
        // Only fully resolved instance methods in the selected API hierarchy are used below.
        try (var files = compiler.getStandardFileManager(null, null, null);
                var paths = Files.walk(Path.of(args[1]))) {
            var sources = paths.filter(path -> path.toString().endsWith(".java")).sorted().toList();
            var options = List.of("-proc:none", "-source", "8", "-target", "8", "-Xlint:-options",
                    "-bootclasspath", args[0], "-classpath", args[2]);
            var task = (JavacTask) compiler.getTask(null, files, diagnostic -> {}, options, null,
                    files.getJavaFileObjectsFromPaths(sources));
            var units = task.parse();
            task.analyze();
            var trees = Trees.instance(task);
            for (CompilationUnitTree unit : units) {
                String source = unit.getSourceFile().getCharContent(false).toString();
                new TreePathScanner<Void, Void>() {
                    @Override
                    public Void visitMethod(MethodTree tree, Void unused) {
                        if (tree.getReturnType() == null
                                || !(trees.getElement(getCurrentPath()) instanceof ExecutableElement method)
                                || !(method.getEnclosingElement() instanceof TypeElement owner)
                                || !method.getModifiers().contains(Modifier.PUBLIC)
                                || method.getModifiers().contains(Modifier.STATIC)) {
                            return null;
                        }
                        String name = internalName(task.getElements(), owner);
                        ApiClass type = api.get(name);
                        if (type == null) {
                            return null;
                        }
                        String descriptor = descriptor(task, method);
                        if (descriptor == null) {
                            return null;
                        }
                        var member = new Method(method.getSimpleName().toString(), descriptor);
                        if (type.methods().containsKey(member)
                                || !isInherited(api, type, member, new HashSet<>())) {
                            return null;
                        }
                        var positions = trees.getSourcePositions();
                        int start = Math.toIntExact(positions.getStartPosition(unit, tree));
                        int end = Math.toIntExact(tree.getBody() == null
                                ? positions.getEndPosition(unit, tree)
                                : positions.getStartPosition(unit, tree.getBody()));
                        String declaration = source.substring(start, end).strip()
                                .replaceAll("\\s+", " ").replaceFirst(";$", "");
                        // Keep the source-built API's existing ability to override these members.
                        declaration = declaration.replaceFirst("\\bpublic final ", "public ");
                        restored.put(name + "\t" + member.name() + "\t" + descriptor, declaration);
                        return null;
                    }
                }.scan(unit, null);
            }
        }
        try (var output = Files.newBufferedWriter(Path.of(args[3]))) {
            for (var entry : restored.entrySet()) {
                output.write(entry.getKey() + "\t" + entry.getValue() + "\n");
            }
        }
        System.out.println("Restored " + restored.size() + " declared public overrides");
    }

    private static Map<String, ApiClass> readApi(Path jar) throws IOException {
        var api = new HashMap<String, ApiClass>();
        try (var zip = new ZipFile(jar.toFile())) {
            for (var entry : zip.stream().filter(item -> item.getName().endsWith(".class")).toList()) {
                try (var stream = zip.getInputStream(entry)) {
                    var reader = new ClassReader(stream);
                    var parents = new ArrayList<>(Arrays.asList(reader.getInterfaces()));
                    if (reader.getSuperName() != null) {
                        parents.add(reader.getSuperName());
                    }
                    var methods = new HashMap<Method, Integer>();
                    reader.accept(new ClassVisitor(Opcodes.ASM9) {
                        @Override
                        public MethodVisitor visitMethod(int access, String name, String descriptor,
                                String signature, String[] exceptions) {
                            methods.put(new Method(name, descriptor), access);
                            return null;
                        }
                    }, ClassReader.SKIP_CODE | ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
                    api.put(reader.getClassName(), new ApiClass(parents, methods));
                }
            }
        }
        return api;
    }

    private static boolean isInherited(Map<String, ApiClass> api, ApiClass type, Method method,
            Set<String> visited) {
        for (String name : type.parents()) {
            ApiClass parent = api.get(name);
            if (parent == null || !visited.add(name)) {
                continue;
            }
            Integer access = parent.methods().get(method);
            if (access != null) {
                if ((access & (Opcodes.ACC_STATIC | Opcodes.ACC_FINAL)) == 0
                        && (access & (Opcodes.ACC_PUBLIC | Opcodes.ACC_PROTECTED)) != 0) {
                    return true;
                }
            } else if (isInherited(api, parent, method, visited)) {
                return true;
            }
        }
        return false;
    }

    private static String descriptor(JavacTask task, ExecutableElement method) {
        var result = new StringBuilder("(");
        for (var parameter : method.getParameters()) {
            String type = descriptor(task.getTypes(), task.getElements(), parameter.asType());
            if (type == null) {
                return null;
            }
            result.append(type);
        }
        String type = descriptor(task.getTypes(), task.getElements(), method.getReturnType());
        return type == null ? null : result.append(')').append(type).toString();
    }

    private static String descriptor(Types types, Elements elements, TypeMirror type) {
        TypeMirror erased = types.erasure(type);
        return switch (erased.getKind()) {
            case BOOLEAN -> "Z";
            case BYTE -> "B";
            case SHORT -> "S";
            case CHAR -> "C";
            case INT -> "I";
            case LONG -> "J";
            case FLOAT -> "F";
            case DOUBLE -> "D";
            case VOID -> "V";
            case ARRAY -> {
                var component = descriptor(types, elements, ((ArrayType) erased).getComponentType());
                yield component == null ? null : "[" + component;
            }
            case DECLARED -> "L" + internalName(elements, (TypeElement) ((DeclaredType) erased).asElement()) + ";";
            default -> null;
        };
    }

    private static String internalName(Elements elements, TypeElement type) {
        return elements.getBinaryName(type).toString().replace('.', '/');
    }
}
