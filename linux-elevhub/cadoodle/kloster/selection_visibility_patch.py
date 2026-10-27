"""Prevent late camera/control callbacks from reviving empty-selection handles."""


def patch_selection_visibility(fetch, replace_once, app_commit, generated):
    """Guard ControlSprites updates against a selection cleared after enqueue.

    ResizingHandle.threeDTarget() posts a JavaFX callback which makes each
    handle visible.  A camera update queued before a tap-to-deselect can
    therefore run after clearSelection() and resurrect the corner handles.
    The live SelectionSession state is authoritative when the callback starts.
    """
    path, text = fetch("CaDoodle-Application", app_commit,
        "src/main/java/com/commonwealthrobotics/controls/ResizingHandle.java", generated)
    text = replace_once(text, "private BowlerStudio3dEngine engine;",
        "private BowlerStudio3dEngine engine;\n\tprivate ControlSprites controlSprites;")
    text = replace_once(text, "\t\tthis.workplane = workplane;",
        "\t\tthis.workplane = workplane;\n\t\tthis.controlSprites = controlSprites;")
    text = replace_once(text,
        "\t\tBowlerStudio.runLater(() -> {\n\t\t\tsetVisible(!locked);",
        "\t\tBowlerStudio.runLater(() -> {\n"
        "\t\t\tif (controlSprites == null || !controlSprites.hasLiveSelection()) {\n"
        "\t\t\t\tsetVisible(false);\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
        "\t\t\tsetVisible(!locked);")
    handle = path
    handle.write_text(text, encoding="utf-8")

    path = generated / "src/main/java/com/commonwealthrobotics/controls/ControlSprites.java"
    text = path.read_text(encoding="utf-8")
    anchor = "\tpublic void updateControls(double screenW, double screenH, double zoom, double az, double el, double x, double y,\n"
    guard = """\t\t// A camera callback may have captured a non-empty selection before the
\t\t// user tapped the selected object again.  Do not call threeDTarget():
\t\t// each ResizingHandle posts a visibility callback and would resurrect
\t\t// the corner handles after clearSelection() has hidden them.
\t\tif (session.getSelected().isEmpty()) {
\t\t\tfor (Node element : allElems)
\t\t\t\telement.setVisible(false);
\t\t\tselectionLive = false;
\t\t\treturn;
\t\t}
"""
    text = replace_once(text, "\tpublic void updateControls(double screenW, double screenH, double zoom, double az, double el, double x, double y,\n",
        "\tboolean hasLiveSelection() {\n"
        "\t\treturn selectionLive && session != null && !session.getSelected().isEmpty();\n"
        "\t}\n\n"
        "\tpublic void updateControls(double screenW, double screenH, double zoom, double az, double el, double x, double y,\n")
    start = text.index(anchor)
    body = text.index("\n", text.index("cameraFovDegrees) {", start)) + 1
    if "if (session.getSelected().isEmpty())" in text[start:body + len(guard) + 200]:
        raise RuntimeError("Selection visibility guard already applied")
    text = text[:body] + guard + text[body:]
    path.write_text(text, encoding="utf-8")
    return [handle, path]
