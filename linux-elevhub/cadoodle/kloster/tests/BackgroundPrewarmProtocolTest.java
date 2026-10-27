package com.commonwealthrobotics.kloster;

/** No-Stage protocol checks; safe to run without a display server. */
public final class BackgroundPrewarmProtocolTest {
    public static void main(String[] args) {
        require(!BackgroundPrewarm.isValidNonce(null), "null nonce accepted");
        require(!BackgroundPrewarm.isValidNonce(""), "empty nonce accepted");
        require(!BackgroundPrewarm.isValidNonce("0123456789abcdef0123456789abcde"), "short nonce accepted");
        require(!BackgroundPrewarm.isValidNonce("0123456789abcdef0123456789abcdef0"), "long nonce accepted");
        require(!BackgroundPrewarm.isValidNonce("0123456789abcdef0123456789abcdeg"), "non-hex nonce accepted");
        require(BackgroundPrewarm.isValidNonce("0123456789abcdef0123456789abcdef"), "valid lowercase nonce rejected");
        require(!BackgroundPrewarm.isValidNonce("ABCDEF0123456789ABCDEF0123456789"), "uppercase nonce accepted");
        System.out.println("PASS: BackgroundPrewarm nonce protocol");
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
