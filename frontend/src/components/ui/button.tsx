import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "../../lib/utils"
import { useGlassPointer } from "../../lib/useGlassPointer"

/**
 * 按钮的玻璃化版本。
 *
 * API 保持不变（variant / size / asChild），因此全站几十处调用点一行都不用改，
 * 却全部换成了同一套材质。悬停浮起与按下压实走 CSS（`.glass--interactive`），
 * 这样 `asChild` 把渲染交给 Slot 时行为依然成立 —— 换成 motion.button 就做不到。
 * 指针高光由 useGlassPointer 写进 CSS 变量。
 */
const buttonVariants = cva(
  cn(
    "relative inline-flex items-center justify-center whitespace-nowrap",
    "text-sm font-medium tracking-tight select-none",
    "disabled:pointer-events-none disabled:opacity-45",
  ),
  {
    variants: {
      variant: {
        default:
          "glass glass--thin glass--grain glass--lit glass--refract glass--interactive glass--tint-primary",
        destructive:
          "glass glass--thin glass--grain glass--lit glass--refract glass--interactive glass--tint-danger",
        outline:
          "glass glass--thin glass--grain glass--lit glass--refract glass--interactive text-foreground",
        secondary:
          "glass glass--thin glass--grain glass--lit glass--interactive text-secondary-foreground",
        ghost:
          "rounded-[var(--glass-radius-sm)] text-foreground/75 transition-colors duration-200 hover:bg-white/10 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        link: "text-primary underline-offset-4 transition-colors hover:underline",
      },
      size: {
        default: "h-10 gap-2 px-4",
        sm: "h-8 gap-1.5 px-3 text-xs",
        lg: "h-12 gap-2.5 px-6 text-base",
        icon: "h-10 w-10 p-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, onPointerMove, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    const trackPointer = useGlassPointer<HTMLButtonElement>()
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        onPointerMove={(event: React.PointerEvent<HTMLButtonElement>) => {
          trackPointer(event)
          onPointerMove?.(event)
        }}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button }
