/**
 * useMounted — false on the server and while hydrating, true once the
 * component is running in the browser.
 *
 * Gates UI that must not be part of the server-rendered HTML (the
 * Leaflet map needs `window`). Reading the flag from an external store
 * with separate server and client snapshots gives the same result as
 * setting state in a mount effect.
 */

"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};
const getSnapshot = () => true;
const getServerSnapshot = () => false;

export function useMounted(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
