import type { ButtonHTMLAttributes } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "link";
};

export function Button({ variant = "default", className, ...rest }: Props) {
  const cls =
    variant === "primary"
      ? "btn btn-primary"
      : variant === "link"
        ? "btn-link"
        : "btn";
  return <button className={[cls, className].filter(Boolean).join(" ")} {...rest} />;
}
