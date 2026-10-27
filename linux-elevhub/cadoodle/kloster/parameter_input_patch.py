"""Use actual editor text for native numeric parameters, with focus commit."""


def patch_parameter_input(replace_once, generated):
    session = generated / "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"
    text = session.read_text(encoding="utf-8")
    start = text.index("\tprivate void setUpNumberField(GridPane gp,")
    end = text.index("\n\tprivate void setUpTextBox(GridPane gp,", start)
    text = text[:start] + '''    private void setUpNumberField(GridPane gp, int line, String text, Parameter para, int width) {
        setUpNumberChoices(gp, line, text, para, width);
    }

    private void setUpNumberChoices(GridPane gp, int line, String text, Parameter para, int width) {
        javafx.scene.control.Control editor = com.commonwealthrobotics.kloster.ParameterNumberEditor.create(
                java.util.List.copyOf(para.getOptions()), MAX_NUMBER_FILED, para::getMM, para::setMM);
        editor.setMinWidth(width);
        gp.add(editor, 1, line);
    }
''' + text[end:]
    text = replace_once(text, "CSG sel = csgs.get(0);\n\t\t\t\tList<String> sortedList", "CSG sel = csgs.get(0);\n                gp.setDisable(sel.isLock());\n\t\t\t\tList<String> sortedList")
    session.write_text(text, encoding="utf-8")
