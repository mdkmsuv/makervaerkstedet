package com.commonwealthrobotics.kloster;

import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;

/**
 * Tracks save attempts by object identity and revision.
 *
 * <p>A save completion can only acknowledge the revision captured when its
 * ticket was created. A later request therefore remains pending even when an
 * older save finishes successfully.</p>
 */
public final class ProjectSaveState<T> {
    private final IdentityHashMap<T, Entry<T>> entries = new IdentityHashMap<>();

    public synchronized void requested(T key) {
        Entry<T> entry = entryFor(key);
        entry.requested++;
    }

    public synchronized Ticket<T> begin(T key) {
        Entry<T> entry = entryFor(key);
        entry.inFlight++;
        return new Ticket<>(entry, key, entry.requested);
    }

    public synchronized void succeeded(Ticket<T> ticket) {
        Entry<T> entry = validate(ticket);
        if (ticket.completed) {
            return;
        }
        ticket.completed = true;
        entry.inFlight--;
        if (ticket.revision > entry.saved) {
            entry.saved = ticket.revision;
        }
        if (entry.failed && ticket.revision >= entry.failureRevision
                && entry.requested <= ticket.revision) {
            entry.failed = false;
            entry.failureRevision = -1;
        }
        removeIfClean(ticket.key, entry);
    }

    public synchronized void failed(Ticket<T> ticket) {
        Entry<T> entry = validate(ticket);
        if (ticket.completed) {
            return;
        }
        ticket.completed = true;
        entry.inFlight--;
        entry.failed = true;
        if (ticket.revision > entry.failureRevision) {
            entry.failureRevision = ticket.revision;
        }
    }

    public synchronized boolean hasPending() {
        for (Entry<T> entry : entries.values()) {
            if (entry.isPending()) {
                return true;
            }
        }
        return false;
    }

    public synchronized List<T> pendingFiles() {
        List<T> pending = new ArrayList<>();
        for (Entry<T> entry : entries.values()) {
            if (entry.isPending()) {
                pending.add(entry.key);
            }
        }
        return pending;
    }

    /** The caller holds the save lock after waiting for every writer. */
    public synchronized void discardPending() {
        if (entries.values().stream().anyMatch(entry -> entry.inFlight > 0))
            throw new IllegalStateException("A save is still running");
        entries.clear();
    }

    private Entry<T> entryFor(T key) {
        Entry<T> entry = entries.get(key);
        if (entry == null) {
            entry = new Entry<>(key);
            entries.put(key, entry);
        }
        return entry;
    }

    private Entry<T> validate(Ticket<T> ticket) {
        if (ticket == null) {
            throw new IllegalArgumentException("ticket must not be null");
        }
        Entry<T> current = entries.get(ticket.key);
        if (current != ticket.entry) {
            throw new IllegalArgumentException("ticket belongs to another save state");
        }
        return current;
    }

    private void removeIfClean(T key, Entry<T> entry) {
        if (!entry.isPending() && entry.inFlight == 0) {
            entries.remove(key);
        }
    }

    private static final class Entry<T> {
        private final T key;
        private long requested;
        private long saved;
        private long failureRevision = -1;
        private int inFlight;
        private boolean failed;

        private Entry(T key) {
            this.key = key;
        }

        private boolean isPending() {
            return inFlight > 0 || requested > saved || failed;
        }
    }

    public static final class Ticket<T> {
        private final Entry<T> entry;
        private final T key;
        private final long revision;
        private boolean completed;

        private Ticket(Entry<T> entry, T key, long revision) {
            this.entry = entry;
            this.key = key;
            this.revision = revision;
        }
    }
}
