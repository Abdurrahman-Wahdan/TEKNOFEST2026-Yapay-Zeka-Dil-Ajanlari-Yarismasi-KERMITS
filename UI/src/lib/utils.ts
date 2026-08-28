import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * The shadcn class merger, for pasted components.
 *
 * Components copied in from shadcn/ui and its ecosystem all import `cn` from
 * this exact path, so the file exists to let them land unedited -- the same
 * reason tailwind.css declares an `app` layer and points `dark:` at the `.dark`
 * class. `clsx` resolves the conditional/array forms, `twMerge` then drops the
 * loser of any two conflicting Tailwind utilities so a caller's `className`
 * beats the component's own default instead of racing it on source order.
 *
 * This is not a general-purpose helper for app code: the app's own components
 * are Vision + MUI and style through `sx` and CSS modules, not utility strings.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
