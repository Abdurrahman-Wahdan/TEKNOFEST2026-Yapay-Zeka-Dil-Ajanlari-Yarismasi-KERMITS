import type { Testimonial } from "@/components/ui/sign-in";
import { BRAND_LOGO } from "@/components/ui/brand";

/**
 * The artwork and quotes shared by the sign-in and sign-up screens.
 *
 * In one place so the two cannot drift: they are meant to be the same screen
 * with different fields, and a hero image changed on one but not the other is
 * the kind of difference nobody notices until a user navigates between them.
 */
export const HERO_IMAGE =
  "https://images.unsplash.com/photo-1642615835477-d303d7dc9ee9?w=2160&q=80";

// These are product highlights, not fabricated customer testimonials. The
// shared KERMİTS mark keeps every auth screen on-brand and avoids loading
// third-party profile photos.
export const TESTIMONIALS: Testimonial[] = [
  {
    avatarSrc: BRAND_LOGO,
    name: "KERMİTS AI",
    handle: "@akilli-asistan",
    text: "Katılım bankacılığı ürünlerini Türkçe yapay zekâ asistanıyla kolayca keşfedin.",
  },
  {
    avatarSrc: BRAND_LOGO,
    name: "Akıllı Karşılaştırma",
    handle: "@kermitsai",
    text: "Bankaların oranlarını, kampanyalarını ve ürün koşullarını tek ekranda karşılaştırın.",
  },
  {
    avatarSrc: BRAND_LOGO,
    name: "Kaynaklı Araştırma",
    handle: "@kermitsai",
    text: "Bilgi tabanı, canlı banka verileri ve güvenilir kaynaklarla açıklanabilir yanıtlar alın.",
  },
];
