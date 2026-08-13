import React, { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';

// --- TYPE DEFINITIONS ---

import type { Testimonial } from './sign-in';

interface ResetPasswordPageProps {
  title?: React.ReactNode;
  description?: React.ReactNode;
  heroImageSrc?: string;
  testimonials?: Testimonial[];
  onReset?: (event: React.FormEvent<HTMLFormElement>) => void;
  onSignIn?: () => void;
}

// --- SUB-COMPONENTS ---

const GlassInputWrapper = ({ children }: { children: React.ReactNode }) => (
  <div className="rounded-2xl border border-border bg-foreground/5 backdrop-blur-sm transition-colors focus-within:border-violet-400/70 focus-within:bg-violet-500/10">
    {children}
  </div>
);

const TestimonialCard = ({ testimonial, delay }: { testimonial: Testimonial, delay: string }) => (
  <div className={`animate-testimonial ${delay} flex items-start gap-3 rounded-3xl bg-card/40 dark:bg-zinc-800/40 backdrop-blur-xl border border-white/10 p-5 w-64`}>
    {/* eslint-disable-next-line @next/next/no-img-element -- matches sign-in.tsx,
        which is kept byte-for-byte as supplied; the two cards must render
        identically. */}
    <img src={testimonial.avatarSrc} className="h-10 w-10 object-cover rounded-2xl" alt="avatar" />
    <div className="text-sm leading-snug">
      <p className="flex items-center gap-1 font-medium">{testimonial.name}</p>
      <p className="text-muted-foreground">{testimonial.handle}</p>
      <p className="mt-1 text-foreground/80">{testimonial.text}</p>
    </div>
  </div>
);

// --- MAIN COMPONENT ---

/**
 * The password-reset screen.
 *
 * Another sibling of `sign-in.tsx`, same reason `sign-up.tsx` is one: that
 * component is kept byte-for-byte as supplied and has no slot for a third
 * mode.
 *
 * Deliberately simple, matching the API behind it: one form, one submission,
 * no separate "check your email" step — this demo's reset endpoint takes the
 * email and the new password in the same request and applies it immediately.
 */
export const ResetPasswordPage: React.FC<ResetPasswordPageProps> = ({
  title = <span className="font-light text-foreground tracking-tighter">Reset Password</span>,
  description = "Enter your email and choose a new password",
  heroImageSrc,
  testimonials = [],
  onReset,
  onSignIn,
}) => {
  const [showPassword, setShowPassword] = useState(false);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');

  // Only a mismatch once the user has actually typed something in the second
  // field — otherwise an empty confirm box reads as an error before they have
  // had a chance to fill it.
  const mismatch = confirm.length > 0 && confirm !== password;

  return (
    <div className="h-[100dvh] flex flex-col md:flex-row font-geist w-[100dvw]">
      {/* Left column: reset form */}
      <section className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-md">
          <div className="flex flex-col gap-6">
            <h1 className="animate-element animate-delay-100 text-4xl md:text-5xl font-semibold leading-tight">{title}</h1>
            <p className="animate-element animate-delay-200 text-muted-foreground">{description}</p>

            <form className="space-y-5" onSubmit={onReset}>
              <div className="animate-element animate-delay-300">
                <label className="text-sm font-medium text-muted-foreground">Email Address</label>
                <GlassInputWrapper>
                  <input name="email" type="email" autoComplete="email" required placeholder="Enter your email address" className="w-full bg-transparent text-sm p-4 rounded-2xl focus:outline-none" />
                </GlassInputWrapper>
              </div>

              <div className="animate-element animate-delay-400">
                <label className="text-sm font-medium text-muted-foreground">New Password</label>
                <GlassInputWrapper>
                  <div className="relative">
                    <input name="password" type={showPassword ? 'text' : 'password'} autoComplete="new-password" required minLength={5} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Create a new password" className="w-full bg-transparent text-sm p-4 pr-12 rounded-2xl focus:outline-none" />
                    <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute inset-y-0 right-3 flex items-center">
                      {showPassword ? <EyeOff className="w-5 h-5 text-muted-foreground hover:text-foreground transition-colors" /> : <Eye className="w-5 h-5 text-muted-foreground hover:text-foreground transition-colors" />}
                    </button>
                  </div>
                </GlassInputWrapper>
              </div>

              <div className="animate-element animate-delay-500">
                <label className="text-sm font-medium text-muted-foreground">Confirm New Password</label>
                <GlassInputWrapper>
                  <div className="relative">
                    <input name="confirmPassword" type={showPassword ? 'text' : 'password'} autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Re-enter your new password" className="w-full bg-transparent text-sm p-4 pr-12 rounded-2xl focus:outline-none" aria-invalid={mismatch || undefined} aria-describedby={mismatch ? 'confirm-error' : undefined} />
                    <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute inset-y-0 right-3 flex items-center">
                      {showPassword ? <EyeOff className="w-5 h-5 text-muted-foreground hover:text-foreground transition-colors" /> : <Eye className="w-5 h-5 text-muted-foreground hover:text-foreground transition-colors" />}
                    </button>
                  </div>
                </GlassInputWrapper>
                {/* Shown only once the field has been filled in, so the error
                    does not appear while the user is still typing the first
                    character of a password that will match. */}
                {mismatch && (
                  <p id="confirm-error" className="mt-2 text-sm text-destructive">Passwords do not match</p>
                )}
              </div>

              <div className="animate-element animate-delay-600 flex items-center justify-end text-sm">
                <span className="text-muted-foreground">At least 5 characters</span>
              </div>

              {/* Disabled on mismatch rather than only checked on submit, same
                  as sign-up: the button going dead the moment the two diverge
                  is the feedback. */}
              <button type="submit" disabled={mismatch} className="animate-element animate-delay-700 w-full rounded-2xl bg-primary py-4 font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:cursor-not-allowed disabled:bg-primary/40 disabled:hover:bg-primary/40">
                Reset Password
              </button>
            </form>

            <p className="animate-element animate-delay-800 text-center text-sm text-muted-foreground">
              Remembered your password? <a href="#" onClick={(e) => { e.preventDefault(); onSignIn?.(); }} className="text-violet-400 hover:underline transition-colors">Sign In</a>
            </p>
          </div>
        </div>
      </section>

      {/* Right column: hero image + testimonials */}
      {heroImageSrc && (
        <section className="hidden md:block flex-1 relative p-4">
          <div className="animate-slide-right animate-delay-300 absolute inset-4 rounded-3xl bg-cover bg-center" style={{ backgroundImage: `url(${heroImageSrc})` }}></div>
          {testimonials.length > 0 && (
            <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex gap-4 px-8 w-full justify-center">
              <TestimonialCard testimonial={testimonials[0]} delay="animate-delay-1000" />
              {testimonials[1] && <div className="hidden xl:flex"><TestimonialCard testimonial={testimonials[1]} delay="animate-delay-1200" /></div>}
              {testimonials[2] && <div className="hidden 2xl:flex"><TestimonialCard testimonial={testimonials[2]} delay="animate-delay-1400" /></div>}
            </div>
          )}
        </section>
      )}
    </div>
  );
};
