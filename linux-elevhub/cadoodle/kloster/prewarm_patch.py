"""Keep startup's logical splash protocol while loading without visible windows."""

def patch_prewarm(fetch, replace_once, engine_commit, generated):
    controller = generated / 'src/main/java/com/commonwealthrobotics/MainController.java'
    text = controller.read_text(encoding='utf-8')
    text = replace_once(text, 'void initialize() {', 'void initialize() {\n'
                        '\t\tcom.commonwealthrobotics.kloster.BackgroundPrewarm.install(newStage);')
    text = replace_once(text, 'BowlerStudio.runLater(() -> session.setKeyBindingFocus());',
                        'BowlerStudio.runLater(() -> {\n'
                        '\t\t\t\t\tcom.commonwealthrobotics.kloster.BackgroundPrewarm.modelReady();\n'
                        '\t\t\t\t\tsession.setKeyBindingFocus();\n\t\t\t\t});')
    controller.write_text(text, encoding='utf-8')
    splash, text = fetch('BowlerStudio', engine_commit,
                        'src/main/java/com/neuronrobotics/bowlerstudio/SplashManager.java', generated)
    waiting = 'com.commonwealthrobotics.kloster.BackgroundPrewarm.isWaiting()'
    text = replace_once(text, 'private static boolean loadFirst = true;',
                        'private static boolean loadFirst = true;\n\tprivate static volatile boolean silentVisible;')
    text = replace_once(text, 'public static void closeSplash() {',
                        f'public static void closeSplash() {{\n\t\tif ({waiting}) {{\n'
                        '\t\t\tif (!closePreventer.getAsBoolean()) silentVisible = false;\n\t\t\treturn;\n\t\t}')
    text = replace_once(text, 'public static boolean isVisibleSplash() {',
                        f'public static boolean isVisibleSplash() {{\n\t\tif ({waiting}) return silentVisible;')
    for signature in ('public static void renderSplashFrame(int percent, String message) {',
                      'public static void onLogUpdate(String message) {'):
        text = replace_once(text, signature, signature + f'\n\t\tif ({waiting}) {{\n'
                            '\t\t\tif (!openPreventer.getAsBoolean()) silentVisible = true;\n\t\t\treturn;\n\t\t}')
    splash.write_text(text, encoding='utf-8')
    return [splash]
