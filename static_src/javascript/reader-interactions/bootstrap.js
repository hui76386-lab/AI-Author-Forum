export default class ReaderInteractionsBootstrap {
    static selector() {
        return '[data-reader-interactions]';
    }

    constructor(element) {
        this.element = element;
        this.loaded = false;
        if ('IntersectionObserver' in window) {
            this.observer = new IntersectionObserver(
                (entries) => {
                    if (entries.some((entry) => entry.isIntersecting)) {
                        this.load();
                    }
                },
                { rootMargin: '320px 0px' },
            );
            this.observer.observe(element);
        } else {
            this.load();
        }
    }

    async load() {
        if (this.loaded) return;
        this.loaded = true;
        if (this.observer) this.observer.disconnect();
        try {
            const module = await import('./app');
            module.mountReaderInteractions(this.element);
        } catch (_error) {
            this.element.dataset.loadFailed = 'true';
        }
    }
}
