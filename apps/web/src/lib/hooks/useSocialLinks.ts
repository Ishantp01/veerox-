import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { SocialLinks } from "@/lib/types";

/**
 * The platform's social-media links, set by the platform admin — shown to
 * every org client (e.g. in the nav footer).
 *
 * GET /billing/social-links → SocialLinks
 */
export function useSocialLinks() {
  return useQuery<SocialLinks>({
    queryKey: queryKeys.socialLinks(),
    queryFn: () => apiFetch<SocialLinks>("/billing/social-links"),
    staleTime: 30_000,
  });
}
