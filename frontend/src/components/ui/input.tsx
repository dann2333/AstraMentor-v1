import * as React from "react"
import { cn } from "../../lib/utils"

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>

/**
 * 输入框做成"凹进玻璃里"的凹槽，和浮起来的按钮形成层次。
 * 聚焦时环形高光从中心扩散，而不是硬切一个描边。
 */
const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "glass-inset flex h-10 w-full px-3.5 py-2 text-sm",
          "text-foreground placeholder:text-muted-foreground/70",
          "border-0 outline-none transition-all duration-300",
          "[transition-timing-function:var(--glass-ease-out)]",
          "focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:ring-offset-0",
          "focus-visible:bg-white/[0.06] focus-visible:shadow-[inset_0_1px_3px_rgba(0,0,0,0.3),0_0_0_3px_hsl(var(--ring)/0.25)]",
          "disabled:cursor-not-allowed disabled:opacity-45",
          "file:border-0 file:bg-transparent file:text-sm file:font-medium",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }
