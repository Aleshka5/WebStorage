import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Mount a viewport layer on document.body.
 *
 * `.view-in` enters with translateY. A transformed ancestor is the containing
 * block for position:fixed, so a dialog rendered inside the view centers on
 * that view instead of the screen.
 */
export function ViewportPortal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body);
}
