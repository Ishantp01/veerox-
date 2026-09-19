import { useAuth } from "@/lib/auth-context";
import { DEFAULT_COUNTRY_CODE } from "@/lib/phone";

/** The signed-in org's default dialing prefix (set when the org was created). */
export function useOrgCountryCode(): string {
  const { user } = useAuth();
  return user?.default_country_code || DEFAULT_COUNTRY_CODE;
}
