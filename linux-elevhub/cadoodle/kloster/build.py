#!/usr/bin/env python3
"""Build a reproducible overlay against the verified official 0.46.094 fat JAR.

Run with the VM's bundled Java 25. The original JAR is never edited in place.
No Gradle/submodule dependency downloads are needed; dependency versions remain pinned.
"""
from pathlib import Path
import argparse
import hashlib
import os
import shutil
import subprocess
import urllib.request
import zipfile
from dimension_input_patch import patch_dimension_input
from move_input_patch import patch_motion
from drag_resize_patch import patch_drag_resize
from touch_input_patch import patch_touch
from close_guard_patch import patch_close_guard
from align_input_patch import patch_align_input
from rotation_input_patch import patch_rotation
from mirror_input_patch import patch_mirror
from workplane_input_patch import patch_workplane_input
from surface_input_patch import patch_surface_input
from pattern_input_patch import patch_pattern_input
from parameter_input_patch import patch_parameter_input
from parameter_regeneration_patch import patch_parameter_regeneration
from string_parameter_patch import patch_string_parameter
from history_input_patch import patch_history
from mutation_order_patch import patch_mutation_order
from selection_visibility_patch import patch_selection_visibility
from prewarm_patch import patch_prewarm

APP_COMMIT = "f723ccd25dbc2caf6c2818ddaa4c47193d75aa6c"
ENGINE_COMMIT = "b3db3606c74344d705c9084af8872cbc12f35a0f"
OFFICIAL_SHA256 = "e51ecdbedc64e98d78c63b05431395a7979d20c76a42b065dede88b3a5918a09"
BASE = Path(__file__).resolve().parent
SOURCE_CACHE = None


def jdk_tool(jdk: Path, name: str) -> Path:
    """Resolve a bundled JDK tool on both Windows and POSIX hosts."""
    windows = jdk / "bin" / f"{name}.exe"
    return windows if windows.is_file() else jdk / "bin" / name


def fetch(repo, commit, filename, directory):
    target = directory / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if SOURCE_CACHE is not None:
        cached = SOURCE_CACHE / repo / commit / filename
        if not cached.is_file():
            raise FileNotFoundError(
                f"Pinned source is missing from cache: {cached}. "
                "Prepare it from the exact repository commit before building.")
        text = cached.read_text(encoding="utf-8")
        target.write_text(text, encoding="utf-8")
        return target, text
    url = f"https://raw.githubusercontent.com/CommonWealthRobotics/{repo}/{commit}/{filename}"
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8")
    target.write_text(text, encoding="utf-8")
    return target, text


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Pinned source no longer matches patch: {old!r}")
    return text.replace(old, new, 1)


def main():
    global SOURCE_CACHE
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--jdk", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-cache", type=Path,
                        help="Use cache/<repo>/<commit>/<path> instead of network fetches.")
    args = parser.parse_args()
    if args.source_cache is not None:
        SOURCE_CACHE = args.source_cache.resolve()
        if not SOURCE_CACHE.is_dir():
            raise SystemExit(f"Pinned source cache directory does not exist: {SOURCE_CACHE}")
    actual = hashlib.file_digest(args.jar.open("rb"), "sha256").hexdigest()
    if actual != OFFICIAL_SHA256:
        raise SystemExit("Use the unmodified official 0.46.094 JAR; hash mismatch.")
    build = args.out.resolve()
    build.mkdir(parents=True, exist_ok=True)
    generated = build / "generated"
    classes = build / "classes"
    # Never ship stale classes from a previous build or a manually compiled probe.
    if classes.exists():
        if classes.is_symlink() or classes.resolve().parent != build.resolve():
            raise SystemExit("Unexpected classes directory; refusing cleanup.")
        shutil.rmtree(classes)
    classes.mkdir(exist_ok=True)
    controller, text = fetch("CaDoodle-Application", APP_COMMIT,
        "src/main/java/com/commonwealthrobotics/MainController.java", generated)
    text = replace_once(text, "ap.resetAllStyleSheets();",
        "ap.resetAllStyleSheets();\n\t\tcom.commonwealthrobotics.kloster.KlosterWorkshop.install(ap, session, engine, totalApplicationBackground, topBar);")
    # ThumbnailImage owns mutable JavaFX state; allocate one provider per file
    # image request instead of sharing MainController.img across worker threads.
    text = replace_once(text,
        "return img.get(instance, incomingToDisplay, destination);",
        "return com.commonwealthrobotics.kloster.WorkshopThumbnails.get(instance, incomingToDisplay, destination);")
    # The existing ruler placement icon and R shortcut also reveal the rulers.
    text = replace_once(text, "void onRuler(ActionEvent event) {",
        "void onRuler(ActionEvent event) {\n\t\tengine.getRulerGroup().setVisible(true);")
    text = replace_once(text, "private static final int ZOOM = -700;",
        "private static final int ZOOM = -330;")
    name_start = text.index("\tprivate void onNameTyped() {")
    name_end = text.index("\n\tprivate void setupCSGEngine()", name_start)
    text = replace_once(text, text[name_start:name_end], """    private javafx.animation.PauseTransition klosterNameDelay;
    private void onNameTyped() {
        final CaDoodleFile editingFile = ap.get();
        if (klosterNameDelay != null) klosterNameDelay.stop();
        klosterNameDelay = new javafx.animation.PauseTransition(javafx.util.Duration.millis(300));
        klosterNameDelay.setOnFinished(event -> {
            if (ap.get() != editingFile || ap.isPreparingProjectFinish()) return;
            editingFile.setProjectName(fileNameBox.getText());
            session.save();
        });
        klosterNameDelay.play();
    }
""")
    text = replace_once(text, "\t\t\tap.loadFromZip(last);", """            com.commonwealthrobotics.kloster.ProjectCloseGuard.requestFinish(totalApplicationBackground, result -> {
                if (!"completed".equals(result)) return;
                Thread loader = new Thread(() -> {
                    CaDoodleFile before = ap.get();
                    ap.loadFromZip(last);
                    if (ap.get() != before) javafx.application.Platform.runLater(() ->
                        com.commonwealthrobotics.kloster.ProjectStartDialog.openedExternally(totalApplicationBackground));
                    else javafx.application.Platform.runLater(() ->
                        com.commonwealthrobotics.kloster.ProjectStartDialog.activate(totalApplicationBackground));
                }, "CaDoodle-open-project-after-finish");
                loader.setDaemon(true); loader.start();
            });""")
    text = replace_once(text,
        "engine.focusOrientation(new TransformNR(0, 0, 0, new RotationNR(0, 15, -45)), new TransformNR(0, 0, 0), ZOOM);",
        "engine.focusOrientation(new TransformNR(0, 0, 0, new RotationNR(0, 0, -45)), new TransformNR(0, 0, 0), ZOOM);")
    controller.write_text(text, encoding="utf-8")
    engine, text = fetch("BowlerStudio", ENGINE_COMMIT,
        "src/main/java/com/neuronrobotics/bowlerstudio/threed/BowlerStudio3dEngine.java", generated)
    subscene = "setSubScene(new SubScene(getRoot(), 10, 10, true, SceneAntialiasing.BALANCED));"
    text = replace_once(text, subscene,
        subscene + "\n\t\tgetSubScene().setFill(Color.web(\"#fafbfc\"));")
    text = replace_once(text, "workplaneGroup = createTexturedWorkplane(1000, 1000);",
        "workplaneGroup = createTexturedWorkplane(180, 180);")
    # Physical millimetre grid stays fixed when users change movement snapping.
    text = replace_once(text, "smallGrid.getTransforms().addAll(gridOffset, gh.mmOffset, gh.SNAP1x);",
        "smallGrid.getTransforms().addAll(gridOffset);")
    text = replace_once(text, "bigGridView.getTransforms().addAll(gh.SNAP10x);",
        "bigGridView.setTranslateZ(0.08); // Separate major/minor depths at crossings; fixed 10 mm grid.")
    text = replace_once(text, "outlineView.getTransforms().addAll(gh.SNAP10x);", "// Fixed physical plate outline.")
    # Both grid meshes sit above the reference surface to avoid depth flicker.
    text = replace_once(text, "gridOffset.setTz(-0.05);", "gridOffset.setTz(0.04);")
    # Sub-pixel hairlines flickered and broke up in the user's normal view.
    # Increase coverage, keep the physical 1/10 mm spacing, and use pale colours.
    text = replace_once(text, "final float SMALL_LINE_WIDTH_MM = PIXEL_SIZE_MM * 2;",
        "final float SMALL_LINE_WIDTH_MM = 0.18f;")
    text = replace_once(text, "final float BIG_LINE_WIDTH_MM = PIXEL_SIZE_MM * 4;",
        "final float BIG_LINE_WIDTH_MM = 0.28f;")
    # A 1 mm near plane gives enough depth precision for the thin grid at normal
    # classroom zoom distances. Avoid drawing minor lines on top of major lines.
    text = replace_once(text, "camera.setNearClip(0.1);", "camera.setNearClip(1.0);")
    # The previous symmetric lights clipped colours and lit a cube's front/top
    # equally. An elevated side key plus weaker fill gives faces distinct tones.
    text = replace_once(text,
        "addPointLight(1000, 0, 1000);\n\t\taddPointLight(-1000, 0, -1000);\n\t\tPointLight follow = addPointLight(1000, -1000, 1000);",
        "PointLight keyLight = addPointLight(800, -1000, 1800);\n"
        "        keyLight.setColor(Color.gray(0.50));\n"
        "        PointLight fillLight = addPointLight(-1400, 900, 600);\n"
        "        fillLight.setColor(Color.gray(0.12));\n"
        "        PointLight follow = addPointLight(1000, -1000, 1000);\n"
        "        follow.setColor(Color.gray(0.20));")
    text = replace_once(text, "ambientLight = new AmbientLight(Color.color(0.4, 0.4, 0.4));",
        "ambientLight = new AmbientLight(Color.color(0.30, 0.30, 0.30));")
    text = replace_once(text, "PointLight cameraLight = new PointLight(Color.color(0.4, 0.4, 0.4));",
        "PointLight cameraLight = new PointLight(Color.color(0.30, 0.30, 0.30));")
    text = replace_once(text, "public void addUserNode(Node n) {",
        "public void addUserNode(Node n) {\n"
        "        if (n instanceof MeshView mesh) com.commonwealthrobotics.kloster.WorkshopSurface.prepare(mesh);")
    text = replace_once(text, "MeshView mesh = currentCsg.getMesh();",
        "MeshView mesh = currentCsg.getMesh();\n"
        "        com.commonwealthrobotics.kloster.WorkshopSurface.prepare(mesh);")
    text = replace_once(text, "Axis axes = new Axis(showAxes ? selected : false);",
        "Axis axes = new Axis(showAxes ? selected : false);\n"
        "                    axes.visibleProperty().bind(rulerGroup.visibleProperty());")
    text = replace_once(text, "outlineMaterial.setDiffuseColor(grid10Color);",
        "javafx.scene.image.WritableImage borderInk = new javafx.scene.image.WritableImage(1, 1);\n"
        "        borderInk.getPixelWriter().setColor(0, 0, Color.web(\"#6aaac4\"));\n"
        "        outlineMaterial.setDiffuseColor(Color.BLACK);\n"
        "        outlineMaterial.setSelfIlluminationMap(borderInk);")
    minor_condition = "if (i > -smallLineCount && i < smallLineCount) {"
    if text.count(minor_condition) != 2:
        raise RuntimeError("Pinned minor grid loops changed.")
    text = text.replace(minor_condition,
        "if (!isBig && i > -smallLineCount && i < smallLineCount) {")
    text = replace_once(text, "final float OUT = 2.0f;", "final float OUT = 0.6f;")
    text = replace_once(text, "int smallLineCount = 40;",
        "int smallLineCount = (int) Math.ceil(Math.max(halfX, halfY));")
    text = replace_once(text, "material.setDiffuseColor(color);", """// Grid colours are a flat reference, independent of model illumination.
            javafx.scene.image.WritableImage ink = new javafx.scene.image.WritableImage(1, 1);
            ink.getPixelWriter().setColor(0, 0, color);
            material.setDiffuseColor(Color.BLACK);
            material.setSelfIlluminationMap(ink);""")
    # Use material alpha for the moved plane, preserving 3D depth and a single
    # transparency layer. Applying opacity to both Group and mesh compounds it.
    # Keep this property across CSS-triggered makeGrid() rebuilds.
    text = replace_once(text, "public static class GridHolder {",
        "public static class GridHolder {\n"
        "\t\tprivate double backgroundOpacity = 1.0;\n"
        "\t\tpublic void setBackgroundOpacity(double opacity) {\n"
        "\t\t\tif (Double.isNaN(opacity) || opacity < 0.0 || opacity > 1.0)\n"
        "\t\t\t\tthrow new IllegalArgumentException(\"background opacity must be 0..1\");\n"
        "\t\t\tbackgroundOpacity = opacity;\n"
        "\t\t\tapplyBackgroundOpacity();\n"
        "\t\t}\n"
        "\t\tpublic double getBackgroundOpacity() { return backgroundOpacity; }\n"
        "\t\tprivate void applyBackgroundOpacity() {\n"
        "\t\t\tbackgroundView.setOpacity(1.0);\n"
        "\t\t\tif (!backgroundView.getChildren().isEmpty()\n"
        "\t\t\t\t\t&& backgroundView.getChildren().get(0) instanceof MeshView mesh\n"
        "\t\t\t\t\t&& mesh.getMaterial() instanceof PhongMaterial material) {\n"
        "\t\t\t\tmaterial.setDiffuseColor(Color.color(0, 0, 0, backgroundOpacity));\n"
        "\t\t\t\t// JavaFX's 3D shader adds emission RGB without multiplying by alpha.\n"
        "\t\t\t\t// Premultiply from the original theme colour, never from the previous map.\n"
        "\t\t\t\tColor base = getLightGrid();\n"
        "\t\t\t\tjavafx.scene.image.WritableImage ink = new javafx.scene.image.WritableImage(1, 1);\n"
        "\t\t\t\tink.getPixelWriter().setColor(0, 0, Color.color(base.getRed() * backgroundOpacity,\n"
        "\t\t\t\t\tbase.getGreen() * backgroundOpacity, base.getBlue() * backgroundOpacity));\n"
        "\t\t\t\tmaterial.setSelfIlluminationMap(ink);\n"
        "\t\t\t}\n"
        "\t\t}")
    text = replace_once(text, "gh.backgroundView.getChildren().add(backgroundView);",
        "gh.backgroundView.getChildren().add(backgroundView);\n"
        "\t\tgh.applyBackgroundOpacity();")
    engine.write_text(text, encoding="utf-8")
    workplane, text = fetch("CaDoodle-Application", APP_COMMIT,
        "src/main/java/com/commonwealthrobotics/WorkplaneManager.java", generated)
    text = replace_once(text, "wpPick = createTexturedWorkplane(200, 200);",
        "wpPick = createTexturedWorkplane(180, 180);")
    text = replace_once(text,
        "return BowlerStudio3dEngine.createTexturedWorkplane(xSizeMM, ySizeMM);",
        "GridHolder grid = BowlerStudio3dEngine.createTexturedWorkplane(xSizeMM, ySizeMM);\n"
        "\t\tgrid.setBackgroundOpacity(0.20);\n"
        "\t\treturn grid;")
    # Surface shading adds per-corner normals. Picking must continue to read
    # the physical triangle points from either supported JavaFX vertex format.
    text = replace_once(text, "private Vector3d toV(javafx.geometry.Point3D p)",
        "private static Vector3d toV(javafx.geometry.Point3D p)")
    text = replace_once(text, "private Polygon getFaceNormalAngles(TriangleMesh mesh, int faceIndex)",
        "private static Polygon getFaceNormalAngles(TriangleMesh mesh, int faceIndex)")
    text = replace_once(text, "int p1Index = faces.get(faceIndex * 6) * 3;",
        "int faceStart = faceIndex * mesh.getFaceElementSize();\n"
        "        int cornerStride = mesh.getFaceElementSize() / 3;\n"
        "        int p1Index = faces.get(faceStart) * 3;")
    text = replace_once(text, "int p2Index = faces.get(faceIndex * 6 + 2) * 3;",
        "int p2Index = faces.get(faceStart + cornerStride) * 3;")
    text = replace_once(text, "int p3Index = faces.get(faceIndex * 6 + 4) * 3;",
        "int p3Index = faces.get(faceStart + 2 * cornerStride) * 3;")
    workplane.write_text(text, encoding="utf-8")
    patch_workplane_input(workplane, replace_once)
    viewcube, text = fetch("CaDoodle-Application", APP_COMMIT,
        "src/main/java/com/commonwealthrobotics/ViewCube.java", generated)
    # Keep the native faces, edge/corner actions and camera mapping. Slimmer
    # geometry and shallow, legible labels remove the oversized scaffold look.
    text = replace_once(text, "new Sphere(10).toCSG()", "new Sphere(5).toCSG()")
    text = replace_once(text, "new Cube(10, cubeSize, 10)", "new Cube(4, cubeSize, 4)")
    text = replace_once(text, "new Cube(10, 10, cubeSize)", "new Cube(4, 4, cubeSize)")
    text = replace_once(text,
        'CSG.textToSize(translation, cubeSize - 20, cubeSize / 2, 4, ActiveProject.getLabelFont("label"))',
        'CSG.textToSize(translation.toUpperCase(java.util.Locale.ROOT), 64, 18, 1.5, '
        'javafx.scene.text.Font.font("Metrophobic", 11))')
    text = replace_once(text, "phongMaterialText.setDiffuseColor(text);",
        'phongMaterialText.setDiffuseColor(Color.web("#627487"));')
    viewcube.write_text(text, encoding="utf-8")
    sources = [controller, engine, workplane, *sorted((BASE / "src").rglob("*.java"))]
    sources.append(viewcube)
    sources.extend(patch_dimension_input(fetch, replace_once, APP_COMMIT, generated))
    patch_drag_resize(generated / "src/main/java/com/commonwealthrobotics/controls/ResizeSessionManager.java")
    sources.extend(patch_motion(fetch, replace_once, APP_COMMIT, generated))
    sources.extend(patch_touch(fetch, replace_once, APP_COMMIT, generated))
    sources.extend(patch_rotation(fetch, replace_once, APP_COMMIT, generated))
    sources.extend(patch_mirror(fetch, replace_once, APP_COMMIT, generated))
    sources.extend(patch_history(fetch, replace_once, generated))
    for patched in patch_close_guard(fetch, replace_once, APP_COMMIT, generated):
        if patched not in sources:
            sources.append(patched)
    for patched in patch_align_input(fetch, replace_once, APP_COMMIT, generated):
        if patched not in sources:
            sources.append(patched)
    for patched in patch_mutation_order(replace_once, generated):
        if patched not in sources:
            sources.append(patched)
    for patched in patch_surface_input(fetch, replace_once, APP_COMMIT, generated):
        if patched not in sources:
            sources.append(patched)
    patch_pattern_input(replace_once, generated)
    patch_parameter_input(replace_once, generated)
    patch_parameter_regeneration(replace_once, generated)
    patch_string_parameter(replace_once, generated)
    for patched in patch_selection_visibility(fetch, replace_once, APP_COMMIT, generated):
        if patched not in sources:
            sources.append(patched)
    sources.extend(patch_prewarm(fetch, replace_once, ENGINE_COMMIT, generated))
    subprocess.run([str(jdk_tool(args.jdk, "javac")), "-encoding", "UTF-8", "-proc:none",
        "--release", "25", "-cp", str(args.jar), "-d", str(classes), *map(str, sources)], check=True)
    # Test the same bundled font that the assembled application will load.
    font_resource = classes / "com/commonwealthrobotics/metrophobic-latin.ttf"
    font_resource.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE / "assets/metrophobic-latin.ttf", font_resource)
    for asset in (BASE / "assets/workplane-labels").glob("*.mesh"):
        target = classes / "com/commonwealthrobotics/workplane-labels" / asset.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, target)
    tests = sorted((BASE / "tests").glob("*.java"))
    if tests:
        classpath = os.pathsep.join((str(classes), str(args.jar)))
        # Windows overlay compatibility marker: f"{classes}:{args.jar}"
        subprocess.run([str(jdk_tool(args.jdk, "javac")), "-encoding", "UTF-8", "-proc:none",
            "-cp", classpath, "-d", str(classes), *map(str, tests)], check=True)
        subprocess.run([str(jdk_tool(args.jdk, "java")), "-Djava.awt.headless=true", "-cp",
            classpath, "SketchGeometryTest", str(build / "fixtures")], check=True)
        subprocess.run([str(jdk_tool(args.jdk, "java")), "-Djava.awt.headless=true", "-cp",
            classpath, "PrintQueueTest"], check=True)
        for test in ("WorkshopRenderingTest", "com.commonwealthrobotics.kloster.WorkshopSurfaceTest",
                     "com.commonwealthrobotics.kloster.WorkplaneReferenceTest", "DimensionResizeTest",
                     "LearningModelsTest", "LearningProjectTest", "GuidedDrawingWorkflowTest",
                     "com.commonwealthrobotics.kloster.ProjectSaveStateTest", "ProjectPersistenceTest",
                     "com.commonwealthrobotics.kloster.ProjectFinishTest"):
            command = [str(jdk_tool(args.jdk, "java")), "-Djava.awt.headless=true"]
            timeout = None
            if test == "LearningModelsTest":
                timeout = 120
            if test in ("LearningProjectTest", "GuidedDrawingWorkflowTest", "ProjectPersistenceTest",
                        "com.commonwealthrobotics.kloster.ProjectFinishTest"):
                profile = build / (test + "-profile")
                profile.mkdir(parents=True, exist_ok=True)
                command.append(f"-Duser.home={profile}")
                timeout = 300
            command.extend(["-cp", classpath, test])
            subprocess.run(command, check=True, timeout=timeout)
    replacements = {str(p.relative_to(classes)).replace("\\", "/"): p.read_bytes()
        for p in classes.rglob("*.class") if not p.name.startswith(tuple(test.stem for test in tests))}
    palette = {"#263d8c": "#333333", "#f2c83d": "#ffffff", "#e7a436": "#eaeaea",
        "#f4ede0": "#f4f3f0", "#f38ba8": "#c62b2f"}
    import re
    with zipfile.ZipFile(args.jar) as original:
        for name in original.namelist():
            if name.startswith("com/commonwealthrobotics/") and name.endswith((".css", ".fxml")):
                text = original.read(name).decode("utf-8")
                for before, after in palette.items():
                    text = re.sub(re.escape(before), after, text, flags=re.IGNORECASE)
                if name.endswith("/stylesheet.css"):
                    text += "\n" + (BASE / "kloster.css").read_text(encoding="utf-8")
                replacements[name] = text.encode("utf-8")
        replacements["com/commonwealthrobotics/metrophobic-latin.ttf"] = (BASE / "assets/metrophobic-latin.ttf").read_bytes()
        for asset in (BASE / "assets/workplane-labels").glob("*.mesh"):
            replacements["com/commonwealthrobotics/workplane-labels/" + asset.name] = asset.read_bytes()
        with zipfile.ZipFile(build / "CaDoodle-Kloster.jar", "w", compression=zipfile.ZIP_DEFLATED) as output:
            for info in original.infolist():
                output.writestr(info, replacements.pop(info.filename, original.read(info.filename)))
            for name, data in replacements.items():
                output.writestr(name, data)
    shutil.copy2(BASE / "assets/Metrophobic-OFL.txt", build / "Metrophobic-OFL.txt")
    print(f"Built {build / 'CaDoodle-Kloster.jar'}")


if __name__ == "__main__":
    main()
