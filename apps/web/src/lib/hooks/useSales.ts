import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { Pipeline, RevenueSummary } from "@/lib/types";

/** GET /sales/pipeline → Pipeline */
export function usePipeline() {
  return useQuery<Pipeline>({
    queryKey: ["sales", "pipeline"],
    queryFn: () => apiFetch<Pipeline>("/sales/pipeline"),
  });
}

/** GET /sales/revenue-summary → RevenueSummary */
export function useRevenueSummary() {
  return useQuery<RevenueSummary>({
    queryKey: ["sales", "revenue-summary"],
    queryFn: () => apiFetch<RevenueSummary>("/sales/revenue-summary"),
  });
}
