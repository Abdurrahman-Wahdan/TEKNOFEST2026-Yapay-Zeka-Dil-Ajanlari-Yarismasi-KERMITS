import type { Testimonial } from "@/components/ui/sign-in";

/**
 * The artwork and quotes shared by the sign-in and sign-up screens.
 *
 * In one place so the two cannot drift: they are meant to be the same screen
 * with different fields, and a hero image changed on one but not the other is
 * the kind of difference nobody notices until a user navigates between them.
 */
export const HERO_IMAGE =
  "https://images.unsplash.com/photo-1642615835477-d303d7dc9ee9?w=2160&q=80";

// Placeholder copy from the component's own demo — not real customers. Replace
// before this is shown to anyone outside the team.
export const TESTIMONIALS: Testimonial[] = [
  {
    avatarSrc: "https://randomuser.me/api/portraits/women/57.jpg",
    name: "Sarah Chen",
    handle: "@sarahdigital",
    text: "Amazing platform! The user experience is seamless and the features are exactly what I needed.",
  },
  {
    avatarSrc: "https://randomuser.me/api/portraits/men/64.jpg",
    name: "Marcus Johnson",
    handle: "@marcustech",
    text: "This service has transformed how I work. Clean design, powerful features, and excellent support.",
  },
  {
    avatarSrc: "https://randomuser.me/api/portraits/men/32.jpg",
    name: "David Martinez",
    handle: "@davidcreates",
    text: "I've tried many platforms, but this one stands out. Intuitive, reliable, and genuinely helpful for productivity.",
  },
];
