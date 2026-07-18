/**
 * Auth shell: centered card, no sidebar. Used by /login so the sign-in screen
 * isn't wrapped in the operator navigation.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas-950 bg-mesh-dark p-6">
      {children}
    </div>
  );
}
