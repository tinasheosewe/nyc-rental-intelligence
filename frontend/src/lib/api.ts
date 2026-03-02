/**
 * API client for the AptHunt backend.
 *
 * Centralizes all HTTP calls. Every component fetches data
 * through these functions rather than calling fetch() directly.
 */

import type { Listing, ListingsResponse, FilterState } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

// ── Generic fetch helper ───────────────────────────────────────

async function apiFetch<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([key, val]) => {
      if (val !== "" && val !== undefined) {
        url.searchParams.set(key, val);
      }
    });
  }

  const res = await fetch(url.toString());
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// ── Listings ───────────────────────────────────────────────────

export async function fetchListings(
  filters: Partial<FilterState> = {},
  sort: string = "composite",
  page: number = 1,
  pageSize: number = 50,
  priorities?: string[],
  kidsMode?: boolean,
): Promise<ListingsResponse> {
  const params: Record<string, string> = {
    page: String(page),
    page_size: String(pageSize),
    sort,
  };

  if (priorities && priorities.length > 0) {
    params.priorities = priorities.join(",");
  }

  if (kidsMode !== undefined) {
    params.kids_mode = String(kidsMode);
  }

  if (filters.beds && filters.beds.length > 0) {
    params.beds = filters.beds.join(",");
  }
  if (filters.minPrice != null) {
    params.min_price = String(filters.minPrice);
  }
  if (filters.maxPrice != null) {
    params.max_price = String(filters.maxPrice);
  }
  if (filters.neighborhoods && filters.neighborhoods.length > 0) {
    params.neighborhoods = filters.neighborhoods.join(",");
  }
  if (filters.rentStabilized != null) {
    params.rent_stabilized = String(filters.rentStabilized);
  }
  if (filters.minScore != null) {
    params.min_score = String(filters.minScore);
  }

  return apiFetch<ListingsResponse>("/listings", params);
}

export async function fetchListing(id: string): Promise<Listing> {
  return apiFetch<Listing>(`/listings/${id}`);
}

export async function fetchNeighborhoods(): Promise<string[]> {
  return apiFetch<string[]>("/neighborhoods");
}
