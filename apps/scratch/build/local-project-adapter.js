// Inserted into render-gui-standalone.jsx after EditorState is created.
// No network calls: the local Elevhub shell stores full editable .sb3 files.
    // Keep signatures stable across equivalent object key insertion orders. The
    // project bridge uses this value to decide whether the current student has
    // changed the editor since the last save.
    const stableStringify = (value) => {
        if (value === null || typeof value !== 'object') return JSON.stringify(value);
        if (Array.isArray(value)) return '[' + value.map(stableStringify).join(',') + ']';
        return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + stableStringify(value[key])).join(',') + '}';
    };

    const installLocalProjects = () => {
        const current = state.store.getState().scratchGui;
        if (!current.vm || !current.projectState.loadingState.startsWith('SHOWING_')) {
            setTimeout(installLocalProjects, 250);
            return;
        }
        const vm = current.vm;
        const parseProject = () => {
            const value = vm.toJSON();
            try { return typeof value === 'string' ? JSON.parse(value) : value; } catch (_) { return value; }
        };
        let baselineSignature = stableStringify(parseProject());
        // Keep the shipped default stage and its assets as the reset baseline.
        // vm.clear() disposes every target and leaves the editor without a stage.
        const initialProject = vm.saveProjectSb3();
        window.ElevhubProjectAdapter = {
            type: 'scratch',
            extension: '.sb3',
            signature: () => stableStringify(parseProject()),
            hasContent: () => stableStringify(parseProject()) !== baselineSignature,
            name: () => state.store.getState().scratchGui.projectTitle || 'Mit Scratch-projekt',
            setName: (value) => state.store.dispatch({type: 'projectTitle/SET_PROJECT_TITLE', title: String(value || 'Mit Scratch-projekt').trim() || 'Mit Scratch-projekt'}),
            export: () => {
                if (!state.store.getState().scratchGui.projectState.loadingState.startsWith('SHOWING_')) {
                    throw new Error('Vent, til Scratch er færdig med at åbne projektet.');
                }
                return vm.saveProjectSb3();
            },
            import: async (blob, name) => {
                await vm.loadProject(await blob.arrayBuffer());
                state.store.dispatch({type: 'projectTitle/SET_PROJECT_TITLE', title: name});
                window.dispatchEvent(new Event('elevhub-project-changed'));
            },
            reset: async () => {
                await vm.loadProject(await (await initialProject).arrayBuffer());
                // Scratch may remap packaged asset ids while loading the same
                // bytes. Establish the post-load representation as baseline.
                baselineSignature = stableStringify(parseProject());
                state.store.dispatch({type: 'projectTitle/SET_PROJECT_TITLE', title: 'Mit Scratch-projekt'});
                window.dispatchEvent(new Event('elevhub-project-changed'));
            }
        };
        vm.on('PROJECT_LOADED', () => window.dispatchEvent(new Event('elevhub-project-changed')));
    };
    installLocalProjects();
