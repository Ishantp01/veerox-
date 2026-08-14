/**
 * Auth shell: centered card, no sidebar. Used by /login so the sign-in screen
 * isn't wrapped in the operator navigation.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-screen overflow-y-auto bg-canvas-950 bg-mesh-dark">
      <div className="flex min-h-full items-center justify-center p-6">{children}</div>
    </div>
  );
}
