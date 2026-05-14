/**
 * Tiny client-side store. Auth token persists in localStorage; everything else
 * is in-memory.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface UIState {
  token: string | null;
  setToken: (t: string | null) => void;
}

export const useUI = create<UIState>()(
  persist(
    (set) => ({
      token: null,
      setToken: (t) => set({ token: t }),
    }),
    { name: "ah-ui" }
  )
);
