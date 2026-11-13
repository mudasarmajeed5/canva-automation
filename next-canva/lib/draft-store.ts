"use client";

import { useCallback, useEffect, useMemo, type Dispatch, type SetStateAction } from "react";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

type DraftState = {
  drafts: Record<string, unknown>;
  hydrated: boolean;
  storageError: boolean;
  setDraft: (key: string, value: unknown) => void;
};

// Storage failures should never prevent editing. Expose them instead of silently
// claiming drafts are saved when the browser has blocked or exhausted storage.
const storage: StateStorage = {
  getItem: (name) => {
    try { return localStorage.getItem(name); }
    catch {
      if (!useDraftStore.getState().storageError) useDraftStore.setState({ storageError: true });
      return null;
    }
  },
  setItem: (name, value) => {
    try { localStorage.setItem(name, value); }
    catch {
      if (!useDraftStore.getState().storageError) useDraftStore.setState({ storageError: true });
    }
  },
  removeItem: (name) => { try { localStorage.removeItem(name); } catch { /* optional cleanup */ } },
};

export const useDraftStore = create<DraftState>()(persist(
  (set) => ({
    drafts: {},
    hydrated: false,
    storageError: false,
    setDraft: (key, value) => set((state) => ({ drafts: { ...state.drafts, [key]: value } })),
  }),
  {
    name: "canva-desk-drafts:v2",
    storage: createJSONStorage(() => storage),
    partialize: (state) => ({ drafts: state.drafts }),
    merge: (persisted, current) => {
      const drafts = (persisted as { drafts?: unknown } | null)?.drafts;
      return { ...current, drafts: drafts && typeof drafts === "object" && !Array.isArray(drafts)
        ? drafts as Record<string, unknown> : {} };
    },
    skipHydration: true,
    onRehydrateStorage: () => (state, error) => {
      useDraftStore.setState({ hydrated: true, storageError: !!error || (state?.storageError ?? false) });
    },
  },
));

export function useStoredState<T>(key: string, initial: () => T, validate: (value: unknown) => value is T) {
  const fallback = useMemo(() => initial(), [initial]);
  const stored = useDraftStore((state) => state.drafts[key]);
  const hydrated = useDraftStore((state) => state.hydrated);
  useEffect(() => {
    if (!useDraftStore.getState().hydrated) void useDraftStore.persist.rehydrate();
  }, []);
  useEffect(() => {
    if (!hydrated || validate(useDraftStore.getState().drafts[key])) return;
    let value = fallback;
    // Import drafts saved by the previous implementation without deleting them.
    try {
      const raw = localStorage.getItem(key);
      const legacy: unknown = raw === null ? null : JSON.parse(raw);
      if (validate(legacy)) value = legacy;
    } catch { /* Invalid legacy drafts use the default. */ }
    useDraftStore.getState().setDraft(key, value);
  }, [key, hydrated, fallback, validate]);

  const setValue: Dispatch<SetStateAction<T>> = useCallback((update) => {
    const current = useDraftStore.getState().drafts[key];
    const previous = validate(current) ? current : fallback;
    const value = typeof update === "function"
      ? (update as (value: T) => T)(previous) : update;
    // Zustand persist writes synchronously on every change, including paste.
    useDraftStore.getState().setDraft(key, value);
  }, [key, fallback, validate]);
  return [validate(stored) ? stored : fallback, setValue, hydrated && validate(stored)] as const;
}
