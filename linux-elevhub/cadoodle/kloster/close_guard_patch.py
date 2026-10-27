"""Patch the pinned ActiveProject save boundary; preserve the Windows locale overlay."""


def patch_close_guard(fetch, replace_once, commit, generated):
    relative = "src/main/java/com/commonwealthrobotics/ActiveProject.java"
    path = generated / relative
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if "projectSaveLock" in text:
            path, text = fetch("CaDoodle-Application", commit, relative, generated)
    else:
        path, text = fetch("CaDoodle-Application", commit, relative, generated)
    text = replace_once(text, "private boolean needsSave = false;", "private volatile boolean needsSave = false;")
    text = replace_once(text, "private boolean saving;", """private volatile boolean saving;
    private volatile boolean closePreparation;
    private final Object projectSaveLock = new Object();
    private final java.util.Set<CaDoodleFile> finishedFiles =
        java.util.Collections.newSetFromMap(new java.util.WeakHashMap<>());
    private final com.commonwealthrobotics.kloster.ProjectSaveState<CaDoodleFile> projectSaves =
        new com.commonwealthrobotics.kloster.ProjectSaveState<>();""")
    working_start = text.index("\tpublic static File getWorkingDir() {")
    working_end = text.index("\n\tpublic List<CaDoodleFile> getProjects()", working_start)
    text = replace_once(text, text[working_start:working_end], """    public static File getWorkingDir() {
        return com.commonwealthrobotics.kloster.ProjectNaming.workingDirectory();
    }
""")
    text = replace_once(text, "fromFile = CaDoodleFile.fromFile(getActiveProject(), this, false);", """File workingFile = com.commonwealthrobotics.kloster.ProjectNaming.workingCopy(getActiveProject());
            ConfigurationDatabase.put("CaDoodle", "CaDoodleActiveFile", workingFile.getAbsolutePath());
            fromFile = CaDoodleFile.fromFile(workingFile, this, false);""")
    start = text.index("\tprivate void save(CaDoodleFile cf) throws SaveOverwriteException {")
    end = text.index("\n\tpublic boolean isOpen()", start)
    text = replace_once(text, text[start:end], """    private void save(CaDoodleFile cf) throws Exception {
        writeProject(cf, false);
    }

    private void writeProject(CaDoodleFile cf, boolean forClose) throws Exception {
        synchronized (projectSaveLock) {
            if (finishedFiles.contains(cf)) return;
            if (closePreparation && !forClose) return;
            var ticket = projectSaves.begin(cf);
            boolean success = false;
            saving = true;
            try {
                if (!cf.isInitialized() || cf.isOperationRunning() || cf.isRegenerating())
                    throw new java.io.IOException("Projektet er stadig ved at blive opdateret.");
                // Keep this file's own path: switching projects must not redirect an old save.
                cf.save();
                // Upstream logs and suppresses this particular failure. Repeat explicitly so
                // a failed parameter database cannot be reported as a successful close-save.
                if (!cf.getCsgDBinstance().getDatabase().isEmpty())
                    cf.getCsgDBinstance().saveDatabase();
                if (!com.commonwealthrobotics.kloster.ProjectPersistence.matchesDisk(cf))
                    throw new java.io.IOException("Projektfilen stemmer ikke med den aktuelle tegning.");
                projectSaves.succeeded(ticket);
                success = true;
            } finally {
                if (!success) projectSaves.failed(ticket);
                saving = false;
            }
        }
    }

    private boolean modelBusyForClose() {
        CaDoodleFile file = fromFile;
        return com.commonwealthrobotics.kloster.ModelEditQueue.isBusy(this)
            || (file != null && (!file.isInitialized()
                || file.isOperationRunning() || file.isRegenerating()));
    }

    public boolean hasUnsavedChanges() {
        CaDoodleFile file = fromFile;
        return saving || projectSaves.hasPending() || modelBusyForClose()
            || (file != null && !com.commonwealthrobotics.kloster.ProjectPersistence.matchesDisk(file))
            || projectSaves.hasPending() || saving;
    }

    public void pauseAutomaticSavingForClose() { closePreparation = true; }

    public boolean isPreparingProjectFinish() { return closePreparation; }

    /** Called only after the finish decision; a fresh file has its own empty history. */
    public void resetProjectAfterFinish() throws Exception {
        synchronized (projectSaveLock) {
            CaDoodleFile previous = fromFile;
            newProject();
            CaDoodleFile blank = fromFile;
            if (blank == null || blank == previous)
                throw new java.io.IOException("En ny tom tegning kunne ikke oprettes.");
            blank.setProjectName("Mit projekt");
            blank.initialize();
            if (!blank.isInitialized() || !blank.getOperations().isEmpty())
                throw new java.io.IOException("Den nye tegning er ikke klar endnu.");
            projectSaves.discardPending();
            if (previous != null) finishedFiles.add(previous);
            needsSave = false;
            writeProject(blank, true);
            com.commonwealthrobotics.kloster.ProjectNaming.cleanupFinishedWorkingCopy(previous, blank);
        }
    }

    public void cancelClosePreparation() {
        closePreparation = false;
        if (projectSaves.hasPending()) needsSave = true;
    }

    /** Run off the FX thread: model commits and thumbnail saving need that thread. */
    public boolean prepareProjectClose(boolean saveChanges) throws Exception {
        closePreparation = true;
        long deadline = System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(30);
        do {
            {
                while (modelBusyForClose()) {
                    if (System.nanoTime() > deadline)
                        throw new java.io.IOException("Tegningen arbejder stadig. Vent lidt og prøv igen.");
                    Thread.sleep(25);
                }
            }
            synchronized (projectSaveLock) {
                // Even discard waits for a write already in progress; never truncate a file.
                if (!saveChanges) return true;
                var files = new java.util.ArrayList<>(projectSaves.pendingFiles());
                CaDoodleFile current = fromFile;
                if (current != null && !files.contains(current)) files.add(current);
                for (CaDoodleFile file : files) writeProject(file, true);
            }
            if (!hasUnsavedChanges()) return true;
            Thread.sleep(25);
        } while (System.nanoTime() < deadline);
        throw new java.io.IOException("Projektet er endnu ikke gemt færdigt. Prøv igen.");
    }
""")
    text = replace_once(text, "\t\tneedsSave = true;", """        CaDoodleFile requestedFile = fromFile;
        if (requestedFile != null) projectSaves.requested(requestedFile);
        needsSave = true;""")
    # The worker must keep the captured file, even if another project opens meanwhile.
    text = replace_once(text, "\t\t\t\t\t\tThread t = new Thread(() -> {", """                        final CaDoodleFile fileToSave = get();
                        Thread t = new Thread(() -> {""")
    text = replace_once(text, "\t\t\t\t\t\t\t\tsave(get());", "\t\t\t\t\t\t\t\tsave(fileToSave);")
    path.write_text(text, encoding="utf-8")
    return [path]
