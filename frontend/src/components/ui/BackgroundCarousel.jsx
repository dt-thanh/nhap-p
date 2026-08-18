import React, { useEffect, useMemo, useRef, useState } from "react";
import { HOMEPAGE_CAROUSEL_IMAGES } from "../../pages/homepageCarouselData";

const ROTATION_MS = 6000;
const TRANSITION_MS = 800;

function isPlaceholderImage(src) {
  return /^PASTE_IMAGE_URL_[1-3]_HERE$/.test(src);
}

export default function BackgroundCarousel({ label = "Bộ sưu tập hình nền" }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [failedImages, setFailedImages] = useState(() => new Set());
  const [reducedMotion, setReducedMotion] = useState(false);
  const [paused, setPaused] = useState(false);
  const carouselRef = useRef(null);

  useEffect(() => {
    const mediaQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mediaQuery) return undefined;

    const updatePreference = () => setReducedMotion(mediaQuery.matches);
    updatePreference();
    mediaQuery.addEventListener?.("change", updatePreference);
    return () => mediaQuery.removeEventListener?.("change", updatePreference);
  }, []);

  useEffect(() => {
    if (paused || reducedMotion) return undefined;
    const timer = window.setInterval(() => {
      setActiveIndex((index) => (index + 1) % HOMEPAGE_CAROUSEL_IMAGES.length);
    }, ROTATION_MS);
    return () => window.clearInterval(timer);
  }, [paused, reducedMotion]);

  const usableImages = useMemo(
    () => HOMEPAGE_CAROUSEL_IMAGES.filter((src) => !isPlaceholderImage(src)),
    [],
  );

  useEffect(() => {
    const firstImage = HOMEPAGE_CAROUSEL_IMAGES[0];
    if (isPlaceholderImage(firstImage)) return undefined;
    const preload = document.createElement("link");
    preload.rel = "preload";
    preload.as = "image";
    preload.href = firstImage;
    document.head.appendChild(preload);
    return () => preload.remove();
  }, []);

  function markImageFailed(src) {
    setFailedImages((images) => {
      const next = new Set(images);
      next.add(src);
      return next;
    });
  }

  function showImage(index) {
    setActiveIndex((index + HOMEPAGE_CAROUSEL_IMAGES.length) % HOMEPAGE_CAROUSEL_IMAGES.length);
  }

  function move(delta) {
    setActiveIndex((index) => (index + delta + HOMEPAGE_CAROUSEL_IMAGES.length) % HOMEPAGE_CAROUSEL_IMAGES.length);
  }

  return (
    <div
      ref={carouselRef}
      className="background-carousel"
      role="region"
      aria-label={label}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={(event) => {
        if (!carouselRef.current?.contains(event.relatedTarget)) setPaused(false);
      }}
    >
      <div className="background-carousel-fallback" aria-hidden="true" />
      {HOMEPAGE_CAROUSEL_IMAGES.map((src, index) => {
        const shouldRender = usableImages.includes(src) && !failedImages.has(src);
        return shouldRender ? (
          <img
            className={`background-carousel-image${index === activeIndex ? " is-active" : ""}`}
            key={src}
            src={src}
            alt=""
            aria-hidden="true"
            loading={index === 0 ? "eager" : "lazy"}
            fetchPriority={index === 0 ? "high" : "auto"}
            onError={() => markImageFailed(src)}
          />
        ) : null;
      })}
      <div className="background-carousel-overlay" aria-hidden="true" />
      <div className="background-carousel-controls">
        <button type="button" aria-label="Ảnh trước" onClick={() => move(-1)}>
          ‹
        </button>
        <div className="background-carousel-dots" aria-label="Chọn hình nền">
          {HOMEPAGE_CAROUSEL_IMAGES.map((_, index) => (
            <button
              type="button"
              key={index}
              aria-label={`Chuyển đến ảnh ${index + 1}`}
              aria-current={index === activeIndex ? "true" : undefined}
              className={index === activeIndex ? "is-active" : ""}
              onClick={() => showImage(index)}
            />
          ))}
        </div>
        <button type="button" aria-label="Ảnh tiếp theo" onClick={() => move(1)}>
          ›
        </button>
      </div>
    </div>
  );
}
