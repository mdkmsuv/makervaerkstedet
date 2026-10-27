package com.commonwealthrobotics.kloster;

import java.util.List;

/** Assertion-based tests for ProjectSaveState; no JavaFX or application setup. */
public final class ProjectSaveStateTest {
    private ProjectSaveStateTest() { }

    public static void main(String[] args) {
        freshStateIsClean();
        requestIsPendingBeforeSaveStarts();
        successfulSaveBecomesClean();
        failureStaysPendingUntilRetrySucceeds();
        changeDuringSaveStaysPending();
        filesAreTrackedByIdentity();
        overlappingSavesNeedAllCompletions();
        System.out.println("PASS: ProjectSaveState revisions, failures, identity and overlap");
    }

    private static void freshStateIsClean() {
        ProjectSaveState<Object> state = new ProjectSaveState<>();
        require(!state.hasPending(), "fresh state is pending");
        require(state.pendingFiles().isEmpty(), "fresh state has files");
    }

    private static void requestIsPendingBeforeSaveStarts() {
        ProjectSaveState<Object> state = new ProjectSaveState<>();
        Object file = new Object();
        state.requested(file);
        require(state.hasPending(), "request before begin was lost");
        require(state.pendingFiles().size() == 1 && state.pendingFiles().get(0) == file,
                "requested file missing from pending list");
    }

    private static void successfulSaveBecomesClean() {
        ProjectSaveState<Object> state = new ProjectSaveState<>();
        Object file = new Object();
        state.requested(file);
        ProjectSaveState.Ticket<Object> ticket = state.begin(file);
        require(state.hasPending(), "in-flight save was not pending");
        state.succeeded(ticket);
        require(!state.hasPending(), "successful save stayed pending");
        require(state.pendingFiles().isEmpty(), "clean entry was retained");
    }

    private static void failureStaysPendingUntilRetrySucceeds() {
        ProjectSaveState<Object> state = new ProjectSaveState<>();
        Object file = new Object();
        ProjectSaveState.Ticket<Object> first = state.begin(file);
        state.failed(first);
        require(state.hasPending(), "failure on clean file was lost");
        ProjectSaveState.Ticket<Object> retry = state.begin(file);
        state.succeeded(retry);
        require(!state.hasPending(), "successful retry stayed pending");
    }

    private static void changeDuringSaveStaysPending() {
        ProjectSaveState<Object> state = new ProjectSaveState<>();
        Object file = new Object();
        state.requested(file);
        ProjectSaveState.Ticket<Object> ticket = state.begin(file);
        state.requested(file);
        state.succeeded(ticket);
        require(state.hasPending(), "later change was acknowledged by old save");
        ProjectSaveState.Ticket<Object> latest = state.begin(file);
        state.succeeded(latest);
        require(!state.hasPending(), "latest revision did not become clean");
    }

    private static void filesAreTrackedByIdentity() {
        ProjectSaveState<Object> state = new ProjectSaveState<>();
        Object first = new String("same");
        Object second = new String("same");
        state.requested(first);
        state.requested(second);
        require(state.pendingFiles().size() == 2, "equal files were merged");
        ProjectSaveState.Ticket<Object> firstSave = state.begin(first);
        state.succeeded(firstSave);
        List<Object> pending = state.pendingFiles();
        require(pending.size() == 1 && pending.get(0) == second,
                "completion for one identity changed another");
    }

    private static void overlappingSavesNeedAllCompletions() {
        ProjectSaveState<Object> state = new ProjectSaveState<>();
        Object file = new Object();
        state.requested(file);
        ProjectSaveState.Ticket<Object> first = state.begin(file);
        ProjectSaveState.Ticket<Object> second = state.begin(file);
        state.succeeded(first);
        require(state.hasPending(), "first overlapping completion cleared second");
        state.succeeded(second);
        require(!state.hasPending(), "second overlapping completion stayed pending");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
