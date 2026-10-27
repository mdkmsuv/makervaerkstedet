"""Serialize parameter commits before mutating the native parameter database."""

def patch_parameter_regeneration(replace_once, generated):
    session = generated / "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"
    text = session.read_text(encoding="utf-8")
    text = replace_once(text, "\tprivate boolean regenerating;", "\tprivate volatile boolean regenerating;" + '''
    private final java.util.Map<String, ParameterCommitBinding> parameterCommitBindings =
            new java.util.concurrent.ConcurrentHashMap<>();
    private final ThreadLocal<Boolean> committingParameter = ThreadLocal.withInitial(() -> false);
    private record ParameterCommitBinding(CaDoodleFile file, CaDoodleOperation source,
            IFileChangeListener listener, File watchedFile) { }

    private void commitParameterValue(Parameter para, double value) {
        parameterCommitter(para).accept(value);
    }

    private java.util.function.DoubleConsumer parameterCommitter(Parameter para) {
        ParameterCommitBinding binding = parameterCommitBindings.get(para.getName());
        if (binding == null || ap.get() != binding.file()) return ignored -> { };
        // Shared parameters can generate siblings; guard the objects whose editor
        // was actually used, rather than every historic output of the operation.
        java.util.List<String> targets = selected.stream()
                .filter(c -> c.getParameters(binding.file().getCsgDBinstance()).contains(para.getName()))
                .map(CSG::getName).toList();
        return value -> commitParameterValue(para, value, binding, targets);
    }

    private void commitParameterValue(Parameter para, double value,
            ParameterCommitBinding binding, java.util.List<String> targets) {
        if (!Double.isFinite(value)) return;
        commitParameterMutation(para, binding, targets,
                current -> Math.abs(current.getMM() - value) < 0.000001,
                current -> current.setMM(value));
    }

    private java.util.function.Consumer<String> stringParameterCommitter(Parameter para) {
        ParameterCommitBinding binding = parameterCommitBindings.get(para.getName());
        if (binding == null || ap.get() != binding.file()) return ignored -> { };
        java.util.List<String> targets = selected.stream()
                .filter(c -> c.getParameters(binding.file().getCsgDBinstance()).contains(para.getName()))
                .map(CSG::getName).toList();
        return value -> {
            if (value == null) return;
            commitParameterMutation(para, binding, targets,
                    current -> java.util.Objects.equals(current.getStrValue(), value),
                    current -> current.setStrValue(value));
        };
    }

    private void commitParameterMutation(Parameter para, ParameterCommitBinding binding,
            java.util.List<String> targets, java.util.function.Predicate<Parameter> unchanged,
            java.util.function.Consumer<Parameter> mutate) {
        if (binding == null || ap.get() != binding.file()) return;
        if (targets.isEmpty()) return;
        com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
            CaDoodleFile file = binding.file();
            try {
                Thread regeneration;
                while (true) {
                    // Never hold the session monitor while waiting: selection
                    // intent callbacks on FX also acquire this monitor.
                    while (regenerating || file.isRegenerating() || file.isOperationRunning()) {
                        if (ap.get() != file) return;
                        Thread.sleep(10);
                    }
                    if (ap.get() != file) return;
                    synchronized (SelectionSession.this) {
                        if (regenerating || file.isRegenerating() || file.isOperationRunning()) continue;
                        if (ap.get() != file || !file.getOperations().contains(binding.source())) return;
                        for (String name : targets) {
                            CSG shape = CaDoodleFile.getByName(file.getCurrentState(), name);
                            if (shape == null || shape.isLock() || shape.isInGroup() || shape.isHide()) return;
                        }
                        Parameter current = file.getCsgDBinstance().get(para.getName());
                        if (current == null || unchanged.test(current)) return;
                        // Suppress only this mutation's synchronous listener;
                        // native regeneration callbacks retain existing guards.
                        committingParameter.set(true);
                        try { mutate.accept(current); }
                        finally { committingParameter.remove(); }
                        regeneration = myRegenerate(binding.source(), binding.listener(), binding.watchedFile());
                        if (regeneration == null) throw new IllegalStateException("Parameter regeneration did not start");
                        break;
                    }
                }
                regeneration.join();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                throw new java.util.concurrent.CompletionException(interrupted);
            } catch (Exception error) {
                throw new java.util.concurrent.CompletionException(error);
            }
        });
    }
''')
    text = replace_once(text, "db.clearParameterListeners(k);\n\t\t\t\t\tdb.addParameterListener(k, (name1, p) -> {", '''db.clearParameterListeners(k);
                    parameterCommitBindings.put(k, new ParameterCommitBinding(ap.get(), source,
                            myL, myFile));
                    db.addParameterListener(k, (name1, p) -> {
                        if (Boolean.TRUE.equals(committingParameter.get())) return;''')
    text = replace_once(text, "MAX_NUMBER_FILED, para::getMM, para::setMM", "MAX_NUMBER_FILED, para::getMM, parameterCommitter(para)")
    # Share the small start critical section with existing file-watcher callers;
    # always release its flag, including null/exception native regeneration.
    start = text.index("\tprivate Thread myRegenerate(")
    end = text.index("\n\tprivate void setUpParametrics(", start)
    method = text[start:end]
    method = replace_once(method, "private Thread myRegenerate(", "private synchronized Thread myRegenerate(")
    method = replace_once(method, "regenerating = true;", "regenerating = true;\n        final CaDoodleFile regenerationFile = ap.get();")
    method = replace_once(method, "Thread tr = new Thread(() -> {", "Thread tr = new Thread(() -> {\n            try {")
    method = replace_once(method, "ap.get().getCsgDBinstance().saveDatabase();", "if (ap.get() != regenerationFile) return;\n                regenerationFile.getCsgDBinstance().saveDatabase();")
    method = replace_once(method, "t = ap.regenerateFrom(source);", "if (ap.get() != regenerationFile) return;\n                t = ap.regenerateFrom(source);")
    method = replace_once(method, "\t\t\tregenerating = false;\n\t\t});", "            } finally { regenerating = false; }\n\t\t});")
    text = text[:start] + method + text[end:]
    session.write_text(text, encoding="utf-8")
