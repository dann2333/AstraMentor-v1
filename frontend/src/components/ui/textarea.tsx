import * as React from "react"
import { cn } from "../../lib/utils"

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          "glass-inset flex min-h-[88px] w-full px-3.5 py-2.5 text-sm leading-relaxed",
          "text-foreground placeholder:text-muted-foreground/70",
          "border-0 outline-none resize-y transition-all duration-300",
          "[transition-timing-function:var(--glass-ease-out)]",
          "focus-visible:bg-white/[0.06] focus-visible:shadow-[inset_0_1px_3px_rgba(0,0,0,0.3),0_0_0_3px_hsl(var(--ring)/0.25)]",
          "disabled:cursor-not-allowed disabled:opacity-45",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Textarea.displayName = "Textarea"

export { Textarea }
