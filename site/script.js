const menuButton = document.querySelector('.nav-toggle');
const navigation = document.querySelector('#site-nav');
const languageLinks = document.querySelectorAll('[data-language]');
const languageStorageKey = 'equiseek-language';

const saveLanguagePreference = (language) => {
  try {
    window.localStorage.setItem(languageStorageKey, language);
  } catch {
    // Language switching still works through ordinary links when storage is unavailable.
  }
};

languageLinks.forEach((link) => {
  link.addEventListener('click', () => {
    saveLanguagePreference(link.dataset.language);
  });
});

menuButton?.addEventListener('click', () => {
  const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
  menuButton.setAttribute('aria-expanded', String(!isOpen));
  navigation?.classList.toggle('is-open', !isOpen);
});

navigation?.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => {
    menuButton?.setAttribute('aria-expanded', 'false');
    navigation.classList.remove('is-open');
  });
});

const revealItems = document.querySelectorAll('.reveal');

if ('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12 },
  );

  revealItems.forEach((item, index) => {
    item.style.transitionDelay = `${Math.min(index % 4, 3) * 70}ms`;
    observer.observe(item);
  });

  window.setTimeout(() => {
    revealItems.forEach((item) => item.classList.add('is-visible'));
  }, 900);
} else {
  revealItems.forEach((item) => item.classList.add('is-visible'));
}
