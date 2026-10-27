package com.commonwealthrobotics.kloster;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.io.File;
import java.io.IOException;
import java.nio.file.*;
import java.util.UUID;

/** Explicit pupil saves are separate from the application's recovery/autosave files. */
public final class ProjectNaming {
    private static final Gson JSON = new Gson();
    private static final String METADATA = "elevhub-project.json";
    private static final java.util.Map<CaDoodleFile, Name> DRAFT_NAMES = java.util.Collections.synchronizedMap(new java.util.WeakHashMap<>());
    private static final java.util.Map<CaDoodleFile, String> DRAFT_IDS = java.util.Collections.synchronizedMap(new java.util.WeakHashMap<>());
    private ProjectNaming() { }

    public record Name(String schoolClass, String firstName, String projectName) {
        public Name {
            schoolClass = component(schoolClass, "klasse", 35);
            firstName = component(firstName, "fornavn", 50);
            if (projectName == null || projectName.isBlank() || projectName.strip().length() > 100
                    || projectName.codePoints().anyMatch(Character::isISOControl))
                throw new IllegalArgumentException("Skriv et gyldigt projektnavn.");
            projectName = projectName.strip();
        }
        public String folder() { return schoolClass + " · " + firstName.replaceAll("\\.+$", ""); }
    }

    public static Path projectsRoot() {
        String override = System.getProperty("elevhub.projects");
        if (override != null && !override.isBlank()) return Path.of(override).toAbsolutePath().normalize();
        return Path.of(System.getProperty("user.home"), "Projekter").toAbsolutePath().normalize();
    }

    public static File workingDirectory() {
        Path folder = projectsRoot().resolve(".elevhub/cadoodle-working");
        try { Files.createDirectories(folder); }
        catch (IOException failure) { throw new IllegalStateException("Arbejdsområdet kunne ikke oprettes.", failure); }
        return folder.toFile();
    }

    /** Copy legacy or explicitly saved files before autosave can change them. */
    public static File workingCopy(File original) throws IOException {
        Path source = original.toPath().toAbsolutePath().normalize();
        Path working = workingDirectory().toPath();
        if (source.startsWith(working)) return original;
        Path destination = working.resolve(UUID.randomUUID().toString());
        copyTree(source.getParent(), destination);
        return destination.resolve(source.getFileName()).toFile();
    }

    private static String component(String value, String label, int limit) {
        if (value == null) throw new IllegalArgumentException("Skriv " + label + ".");
        if (value.codePoints().anyMatch(Character::isISOControl)) throw new IllegalArgumentException("Skriv et gyldigt " + label + ".");
        String clean = value.strip().replaceAll("\\s+", " ");
        if (clean.isEmpty() || clean.length() > limit || clean.matches(".*[\\p{Cntrl}/\\\\:*?\"<>|].*")
                || clean.equals(".") || clean.equals(".."))
            throw new IllegalArgumentException("Skriv et gyldigt " + label + ".");
        return clean;
    }

    private static JsonObject metadata(CaDoodleFile file) {
        try {
            Path path = file.getSelf().toPath().getParent().resolve(METADATA);
            if (Files.size(path) > 1024 * 1024) return null;
            JsonObject data = JsonParser.parseString(Files.readString(path)).getAsJsonObject();
            if (!data.get("id").getAsString().matches("[a-f0-9]{32}")) return null;
            new Name(data.get("class").getAsString(), data.get("firstName").getAsString(), data.get("name").getAsString());
            return data;
        } catch (Exception ignored) { return null; }
    }

    public static Name name(CaDoodleFile file) {
        if (file == null) return null;
        JsonObject data = metadata(file);
        try {
            Name draft = DRAFT_NAMES.get(file);
            if (draft != null) return new Name(draft.schoolClass(), draft.firstName(), file.getMyProjectName());
            return data == null ? null : new Name(data.get("class").getAsString(), data.get("firstName").getAsString(),
                    file.getMyProjectName());
        } catch (IllegalArgumentException invalidTitle) { return null; }
    }

    /** Naming a new session never publishes an empty project in the pupil archive. */
    public static void nameDraft(CaDoodleFile file, Name name) {
        file.setProjectName(name.projectName()); DRAFT_NAMES.put(file, name);
    }

    private static String signature(CaDoodleFile file) {
        JsonObject state = new JsonObject();
        state.add("project", JsonParser.parseString(file.toJson()));
        JsonObject parameters = JsonParser.parseString(file.getCsgDBinstance().getDataBaseString()).getAsJsonObject();
        Path directory = file.getSelf().toPath().toAbsolutePath().normalize().getParent();
        // Native regeneration rewrites its imported-file parameters after a project
        // is copied. Compare their location inside the project, not its current folder.
        for (var entry : parameters.entrySet()) {
            if (!entry.getKey().endsWith("_CaDoodle_File") || !entry.getValue().isJsonObject()) continue;
            JsonObject parameter = entry.getValue().getAsJsonObject();
            if (!parameter.has("strValue") || !parameter.get("strValue").isJsonPrimitive()) continue;
            try {
                Path path = Path.of(parameter.get("strValue").getAsString());
                if (path.isAbsolute() && path.normalize().startsWith(directory))
                    parameter.addProperty("strValue", "@project/" + directory.relativize(path.normalize()).toString().replace('\\', '/'));
            } catch (InvalidPathException ignored) { }
        }
        state.add("parameters", parameters);
        return JSON.toJson(state);
    }

    public static boolean hasUnpublishedChanges(CaDoodleFile file) {
        if (file == null) return false;
        JsonObject data = metadata(file);
        if (data == null) return !file.getOperations().isEmpty();
        try { return !JsonParser.parseString(signature(file)).equals(data.get("savedState")); }
        catch (Exception failure) { return true; }
    }

    /** All files are copied and verified before the visible snapshot replaces the old one. */
    public static Path publish(CaDoodleFile file, Name name) throws IOException {
        if (!ProjectPersistence.matchesDisk(file)) throw new IOException("Projektet er ikke gemt færdigt.");
        JsonObject previous = metadata(file);
        String id = previous == null ? DRAFT_IDS.computeIfAbsent(file, ignored -> UUID.randomUUID().toString().replace("-", ""))
            : previous.get("id").getAsString();
        JsonObject data = new JsonObject();
        data.addProperty("version", 1); data.addProperty("id", id);
        data.addProperty("class", name.schoolClass()); data.addProperty("firstName", name.firstName());
        data.addProperty("name", file.getMyProjectName());
        data.add("savedState", JsonParser.parseString(signature(file)));
        Path source = file.getSelf().toPath().getParent();
        Path parent = projectsRoot().resolve(name.folder());
        Files.createDirectories(parent);
        Path target = parent.resolve(id);
        if (Files.isSymbolicLink(parent) || Files.isSymbolicLink(target)) throw new IOException("Gemmemappen må ikke være et link.");
        if (Files.exists(target)) {
            try {
                JsonObject current = JsonParser.parseString(Files.readString(target.resolve(METADATA))).getAsJsonObject();
                if ((previous == null || !java.util.Objects.equals(current.get("savedState"), previous.get("savedState")))
                        && !java.util.Objects.equals(current.get("savedState"), data.get("savedState")))
                    throw new IOException("En nyere version er allerede gemt. Åbn den fra Dine projekter.");
            } catch (RuntimeException failure) { throw new IOException("Den tidligere gemning kunne ikke kontrolleres.", failure); }
        }
        Path staged = parent.resolve(".save-" + UUID.randomUUID());
        Path backup = parent.resolve(".previous-" + UUID.randomUUID());
        boolean movedPrevious = false;
        boolean committed = false;
        Path temp = source.resolve("." + METADATA + ".tmp");
        try {
            copyTree(source, staged);
            Files.writeString(staged.resolve(METADATA), JSON.toJson(data));
            if (!Files.readString(staged.resolve(file.getSelf().getName())).equals(Files.readString(file.getSelf().toPath())))
                throw new IOException("Den gemte kopi kunne ikke kontrolleres.");
            Files.writeString(temp, JSON.toJson(data));
            if (Files.exists(target)) { Files.move(target, backup, StandardCopyOption.ATOMIC_MOVE); movedPrevious = true; }
            Files.move(staged, target, StandardCopyOption.ATOMIC_MOVE);
            committed = true;
            Files.move(temp, source.resolve(METADATA), StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException failure) {
            if (committed) deleteOwnedTree(parent, target);
            if (movedPrevious && !Files.exists(target)) Files.move(backup, target, StandardCopyOption.ATOMIC_MOVE);
            deleteOwnedTree(parent, staged);
            Files.deleteIfExists(temp);
            throw failure;
        }
        deleteOwnedTree(parent, backup);
        return target;
    }

    public static void cleanupFinishedWorkingCopy(CaDoodleFile previous, CaDoodleFile current) throws IOException {
        if (previous == null || current == null) return;
        Path working = workingDirectory().toPath().toAbsolutePath().normalize();
        Path folder = previous.getSelf().toPath().toAbsolutePath().normalize().getParent();
        Path active = current.getSelf().toPath().toAbsolutePath().normalize();
        if (folder != null && !folder.equals(working) && folder.startsWith(working) && !active.startsWith(folder))
            deleteOwnedTree(working, folder);
    }

    private static void deleteOwnedTree(Path boundary, Path folder) throws IOException {
        Path parent = boundary.toAbsolutePath().normalize(), target = folder.toAbsolutePath().normalize();
        if (target.equals(parent) || !target.startsWith(parent)) throw new IOException("Uventet oprydningsmappe.");
        if (!Files.exists(target, LinkOption.NOFOLLOW_LINKS)) return;
        if (Files.isSymbolicLink(target) || !target.toRealPath().startsWith(parent.toRealPath()))
            throw new IOException("Oprydningsmappen må ikke være et link.");
        try (var paths = Files.walk(target)) {
            for (Path path : paths.sorted(java.util.Comparator.reverseOrder()).toList()) Files.delete(path);
        }
    }

    public static java.util.List<String> knownClasses() {
        java.util.TreeSet<String> classes = new java.util.TreeSet<>();
        try (var folders = Files.list(projectsRoot())) {
            for (Path folder : folders.filter(Files::isDirectory).toList()) {
                String label = folder.getFileName().toString(); int separator = label.indexOf(" · ");
                if (separator > 0) classes.add(component(label.substring(0, separator), "klasse", 35));
            }
        } catch (IOException | IllegalArgumentException ignored) { }
        return java.util.List.copyOf(classes);
    }

    private static void copyTree(Path source, Path destination) throws IOException {
        if (Files.isSymbolicLink(source)) throw new IOException("Projektmappen må ikke være et link.");
        Files.createDirectories(destination);
        try (var paths = Files.walk(source)) {
            for (Path path : paths.filter(path -> !path.startsWith(destination) &&
                    source.relativize(path).iterator().hasNext() &&
                    java.util.stream.StreamSupport.stream(source.relativize(path).spliterator(), false)
                        .noneMatch(part -> part.toString().startsWith("."))).toList()) {
                if (Files.isSymbolicLink(path)) throw new IOException("Projektfiler må ikke være links.");
                Path target = destination.resolve(source.relativize(path));
                if (Files.isDirectory(path)) Files.createDirectories(target);
                else if (Files.isRegularFile(path)) {
                    Files.copy(path, target, StandardCopyOption.REPLACE_EXISTING);
                    if (Files.mismatch(path, target) != -1) throw new IOException("Projektkopien kunne ikke kontrolleres.");
                }
            }
        }
    }
}
