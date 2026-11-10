define('custom:views/lead-list', ['views/list'], function (ListView) {
    const statuses = [
        ['', 'Все'], ['New', 'Новый'], ['In Process', 'В работе'],
        ['Send Offer', 'Отправить оффер'], ['Cold Email', 'Cold-email'],
        ['Converted', 'Успешно'], ['Recycled', 'Перезвонить позже'], ['Dead', 'Отказ'],
    ];

    return class extends ListView {
        afterRender() {
            super.afterRender();
            this.renderStatusTabs();
        }

        async renderStatusTabs() {
            const host = this.element.querySelector('.search-container');
            if (!host) return;
            let bar = host.querySelector('.lead-status-tabs');
            if (!bar) {
                bar = document.createElement('div');
                bar.className = 'lead-status-tabs btn-group';
                bar.setAttribute('role', 'group');
                host.prepend(bar);
            }
            bar.replaceChildren();
            const counts = await Promise.all(statuses.map(async ([status]) => {
                const collection = await this.getCollectionFactory().create('Lead');
                collection.maxSize = 1;
                collection.where = status ? [{type: 'equals', attribute: 'status', value: status}] : [];
                await collection.fetch();
                return collection.total || 0;
            }));
            statuses.forEach(([status, label], index) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-default' + ((this.collection.whereAdditional || []).some(w => w.attribute === 'status' && w.value === status) || (!status && !(this.collection.whereAdditional || []).some(w => w.attribute === 'status')) ? ' active' : '');
                button.textContent = `${label} (${counts[index]})`;
                button.onclick = () => {
                    this.collection.whereAdditional = status ? [{type: 'equals', attribute: 'status', value: status}] : [];
                    this.collection.isFetched = false;
                    this.collection.reset();
                    this.clearView('list');
                    this.loadList();
                    bar.querySelectorAll('button').forEach(item => item.classList.remove('active'));
                    button.classList.add('active');
                };
                bar.append(button);
            });
        }
    };
});
