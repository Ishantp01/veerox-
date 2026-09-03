/**
 * Auth shell: no sidebar. Used by /login and /forgot-token so those screens
 * aren't wrapped in the operator navigation. Background/centering is left to
 * each page since /login is a full-bleed two-panel layout while /forgot-token
 * is a centered card.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return <div className="h-screen overflow-y-auto">{children}</div>;
}
