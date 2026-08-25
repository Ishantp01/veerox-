/**
 * Barrel for the UI primitive library (UI plan §7.1). Lets pages import from a
 * single path, e.g. `import { Button, Card, Badge } from "@/components/ui"`.
 *
 * Note: `Button` is also a default export of `./button` for backward compat
 * with existing pages (`import Button from "@/components/ui/button"`).
 */

export { default as Button, Button as ButtonComponent, buttonVariants } from "./button";
export type { ButtonProps } from "./button";

export { Card, CardHeader, CardTitle, CardContent, CardFooter } from "./card";

export { Table, TableHeader, TableRow, TableCell } from "./table";

export { Input, Textarea, Label } from "./input";
export type { InputProps, TextareaProps, LabelProps } from "./input";

export { Select } from "./select";
export type { SelectProps } from "./select";

export { Badge, badgeVariants } from "./badge";
export type { BadgeProps, BadgeVariant } from "./badge";

export { Skeleton, SkeletonRows } from "./skeleton";
export type { SkeletonRowsProps } from "./skeleton";

export { Spinner } from "./spinner";
export type { SpinnerProps } from "./spinner";

export { EmptyState } from "./empty-state";
export type { EmptyStateProps } from "./empty-state";

export {
  ToastProvider,
  Toaster,
  useToast,
} from "./toast";
export type { ToastOptions, ToastVariant } from "./toast";

export {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  DialogClose,
} from "./dialog";
export type { DialogProps, DialogContentProps, DialogTriggerProps } from "./dialog";

export { ConfirmProvider, useConfirm } from "./confirm-dialog";
export type { ConfirmOptions } from "./confirm-dialog";
