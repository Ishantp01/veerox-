// Shared phone helpers. Every input that takes a customer's number accepts a
// local number and adds the org's default country code (chosen when the org
// was created) — mirrors apps/api/core/phone.py so the UI previews exactly
// what the server will store.
export const E164_REGEX = /^\+\d{8,15}$/;
export const E164_MESSAGE = "Enter a valid E.164 number, e.g. +919876543210";
export const PHONE_MESSAGE = "Enter a valid phone number";

export const DEFAULT_COUNTRY_CODE = "+91";

/** Dialing prefixes for the org country-code dropdowns (any other code can be typed via "Other"). */
export const COUNTRY_CODE_OPTIONS: { code: string; label: string }[] = [
  { code: "+91", label: "India (+91)" },
  { code: "+1", label: "United States / Canada (+1)" },
  { code: "+44", label: "United Kingdom (+44)" },
  { code: "+971", label: "United Arab Emirates (+971)" },
  { code: "+966", label: "Saudi Arabia (+966)" },
  { code: "+974", label: "Qatar (+974)" },
  { code: "+965", label: "Kuwait (+965)" },
  { code: "+968", label: "Oman (+968)" },
  { code: "+973", label: "Bahrain (+973)" },
  { code: "+65", label: "Singapore (+65)" },
  { code: "+60", label: "Malaysia (+60)" },
  { code: "+62", label: "Indonesia (+62)" },
  { code: "+66", label: "Thailand (+66)" },
  { code: "+84", label: "Vietnam (+84)" },
  { code: "+63", label: "Philippines (+63)" },
  { code: "+852", label: "Hong Kong (+852)" },
  { code: "+86", label: "China (+86)" },
  { code: "+81", label: "Japan (+81)" },
  { code: "+82", label: "South Korea (+82)" },
  { code: "+61", label: "Australia (+61)" },
  { code: "+64", label: "New Zealand (+64)" },
  { code: "+92", label: "Pakistan (+92)" },
  { code: "+880", label: "Bangladesh (+880)" },
  { code: "+94", label: "Sri Lanka (+94)" },
  { code: "+977", label: "Nepal (+977)" },
  { code: "+975", label: "Bhutan (+975)" },
  { code: "+960", label: "Maldives (+960)" },
  { code: "+93", label: "Afghanistan (+93)" },
  { code: "+49", label: "Germany (+49)" },
  { code: "+33", label: "France (+33)" },
  { code: "+39", label: "Italy (+39)" },
  { code: "+34", label: "Spain (+34)" },
  { code: "+351", label: "Portugal (+351)" },
  { code: "+31", label: "Netherlands (+31)" },
  { code: "+32", label: "Belgium (+32)" },
  { code: "+41", label: "Switzerland (+41)" },
  { code: "+43", label: "Austria (+43)" },
  { code: "+46", label: "Sweden (+46)" },
  { code: "+47", label: "Norway (+47)" },
  { code: "+45", label: "Denmark (+45)" },
  { code: "+358", label: "Finland (+358)" },
  { code: "+353", label: "Ireland (+353)" },
  { code: "+48", label: "Poland (+48)" },
  { code: "+7", label: "Russia (+7)" },
  { code: "+90", label: "Turkey (+90)" },
  { code: "+30", label: "Greece (+30)" },
  { code: "+972", label: "Israel (+972)" },
  { code: "+20", label: "Egypt (+20)" },
  { code: "+27", label: "South Africa (+27)" },
  { code: "+234", label: "Nigeria (+234)" },
  { code: "+254", label: "Kenya (+254)" },
  { code: "+233", label: "Ghana (+233)" },
  { code: "+255", label: "Tanzania (+255)" },
  { code: "+256", label: "Uganda (+256)" },
  { code: "+251", label: "Ethiopia (+251)" },
  { code: "+212", label: "Morocco (+212)" },
  { code: "+55", label: "Brazil (+55)" },
  { code: "+54", label: "Argentina (+54)" },
  { code: "+56", label: "Chile (+56)" },
  { code: "+57", label: "Colombia (+57)" },
  { code: "+51", label: "Peru (+51)" },
  { code: "+52", label: "Mexico (+52)" },
];

/** Same rules as apps/api/core/phone.py::normalize_phone. */
export function normalizePhone(raw: string, countryCode: string = DEFAULT_COUNTRY_CODE): string {
  const text = (raw ?? "").trim();
  if (!text) return "";
  const digits = text.replace(/\D/g, "");
  if (!digits) return "";
  if (text.startsWith("+")) return `+${digits}`;
  if (digits.startsWith("00")) return `+${digits.slice(2)}`;

  const codeDigits = (countryCode || DEFAULT_COUNTRY_CODE).replace(/\D/g, "");
  const national = digits.startsWith("0") ? digits.replace(/^0+/, "") : digits;
  if (digits.startsWith(codeDigits) && digits.length >= codeDigits.length + 10) {
    return `+${digits}`;
  }
  return `+${codeDigits}${national}`;
}

/** True when `raw` becomes a valid E.164 number once the country code is added. */
export function isValidPhone(raw: string, countryCode: string = DEFAULT_COUNTRY_CODE): boolean {
  return E164_REGEX.test(normalizePhone(raw, countryCode));
}
