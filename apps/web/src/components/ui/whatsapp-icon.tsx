import { SVGProps } from "react";
import { cn } from "@/lib/utils";

export interface WhatsappIconProps extends Omit<SVGProps<SVGSVGElement>, "size"> {
  size?: number | string;
  /** Subtle breathing pulse so the glyph reads as "live" — opt in per call
   * site (conversation/lead rows) rather than on by default, since a
   * persistent nav icon pulsing forever would be more distracting than
   * useful. */
  animated?: boolean;
}

/**
 * The actual WhatsApp glyph (brand phone-in-speech-bubble mark), filled with
 * `currentColor` so it inherits whatever text color the caller sets — used
 * in place of the generic lucide `MessageSquare` wherever we specifically
 * mean the WhatsApp channel (badges, nav, conversation lists).
 */
export function WhatsappIcon({
  size = 16,
  className,
  animated = false,
  ...props
}: WhatsappIconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden
      className={cn("shrink-0", animated && "animate-whatsapp-pulse", className)}
      {...props}
    >
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.372-.025-.521-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.095 3.2 5.076 4.487.71.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z" />
      <path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.85.5 3.577 1.36 5.06L2 22l5.16-1.45a9.9 9.9 0 0 0 4.88 1.28c5.46 0 9.91-4.45 9.91-9.92C21.95 6.45 17.5 2 12.04 2Zm0 18.02a8.08 8.08 0 0 1-4.13-1.13l-.296-.176-3.06.86.82-2.98-.194-.307a8.08 8.08 0 0 1-1.24-4.31c0-4.47 3.63-8.1 8.1-8.1 4.47 0 8.1 3.63 8.1 8.1 0 4.47-3.63 8.1-8.1 8.1Z" />
    </svg>
  );
}

export default WhatsappIcon;
