define('custom:views/lead-detail', ['views/record/detail', 'moment'], function (DetailRecordView, moment) {
    const statuses = ['New', 'In Process', 'Send Offer', 'Cold Email', 'Converted', 'Recycled', 'Dead'];

    return class extends DetailRecordView {
        events = Object.assign({}, super.events, {
            'keydown .field input, .field textarea, .field select': function (event) {
                if (event.key !== 'Enter') return;
                const target = event.target;
                if (target.tagName === 'TEXTAREA' && event.shiftKey) return;
                if (target.tagName === 'INPUT' && ['button', 'submit', 'checkbox', 'radio'].includes(target.type)) return;
                event.preventDefault();
                event.stopPropagation();
                if (this.mode === this.MODE_EDIT) {
                    this.actionSave();
                    return;
                }
                const field = target.closest('.field[data-name]');
                const name = field && field.dataset.name;
                if (name && this.inlineEditModeIsOn) this.recordHelper.trigger('inline-edit-save', name);
            },
        });

        setup() {
            super.setup();
            this.listenTo(this.model, 'change:status', () => this.updateStatusButtons());
            this.on('after:mode-change', () => {
                this.updateStatusButtons();
                this.updateCallbacks();
            });
        }

        afterRender() {
            super.afterRender();
            this.renderStatusButtons();
            this.renderCallbacks();
        }

        // Callbacks are ordinary Tasks linked to the lead: dateEnd is the call time, description the note.
        renderCallbacks() {
            const host = this.element && this.element.querySelector('.middle');
            if (!host || host.querySelector('.lead-callbacks')) return;
            const box = document.createElement('div');
            box.className = 'lead-callbacks panel panel-default';
            box.style.cssText = 'margin-bottom:12px';
            box.innerHTML = `
                <div class="panel-heading"><h4 class="panel-title">Перезвоны</h4></div>
                <div class="panel-body">
                    <form class="lead-callback-form" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">
                        <input type="date" class="form-control" name="date" required style="width:auto" aria-label="Дата перезвона">
                        <input type="time" class="form-control" name="time" required style="width:auto" aria-label="Время перезвона">
                        <input type="text" class="form-control" name="note" maxlength="500" placeholder="Что сказать"
                               style="flex:1 1 220px;width:auto" aria-label="Что сказать">
                        <button type="submit" class="btn btn-primary">Назначить перезвон</button>
                    </form>
                    <ul class="lead-callback-list list-unstyled" style="margin:10px 0 0"></ul>
                </div>`;
            const status = host.querySelector('.lead-status-actions');
            status ? status.after(box) : host.prepend(box);
            const form = box.querySelector('form');
            // Today, an hour from now rounded to 15 minutes; after 20:00 — tomorrow at 10:00.
            let next = moment().tz(this.timeZone()).add(1, 'hour');
            next.minutes(Math.ceil(next.minutes() / 15) * 15).seconds(0);
            if (next.hours() >= 21 || next.date() !== moment().tz(this.timeZone()).date()) {
                next = moment().tz(this.timeZone()).add(1, 'day').hours(10).minutes(0);
            }
            form.date.value = next.format('YYYY-MM-DD');
            form.time.value = next.format('HH:mm');
            form.onsubmit = event => {
                event.preventDefault();
                this.createCallback(form);
            };
            this.updateCallbacks();
            this.loadCallbacks();
        }

        timeZone() {
            return this.getDateTime().getTimeZone();
        }

        updateCallbacks() {
            const box = this.element && this.element.querySelector('.lead-callbacks');
            if (box) box.style.display = this.mode === this.MODE_EDIT ? 'none' : '';
        }

        async loadCallbacks() {
            const list = this.element && this.element.querySelector('.lead-callback-list');
            if (!list) return;
            const result = await Espo.Ajax.getRequest('Task', {
                select: 'name,dateEnd,description,status', orderBy: 'dateEnd', order: 'asc', maxSize: 20,
                where: [
                    {type: 'equals', attribute: 'parentType', value: 'Lead'},
                    {type: 'equals', attribute: 'parentId', value: this.model.id},
                    {type: 'in', attribute: 'status', value: ['Not Started', 'Started']},
                ],
            });
            list.replaceChildren();
            if (!result.list.length) {
                const empty = document.createElement('li');
                empty.className = 'text-muted';
                empty.textContent = 'Перезвонов не назначено';
                list.append(empty);
            }
            result.list.forEach(task => {
                const item = document.createElement('li');
                item.dataset.id = task.id;
                item.style.cssText = 'display:flex;gap:8px;align-items:center;padding:4px 0';
                const when = document.createElement('strong');
                const due = task.dateEnd ? moment.utc(task.dateEnd).tz(this.timeZone()) : null;
                const now = moment().tz(this.timeZone());
                const day = !due ? '' : due.isSame(now, 'day') ? 'Сегодня' :
                    due.isSame(now.clone().add(1, 'day'), 'day') ? 'Завтра' : due.format('DD.MM');
                when.textContent = due ? day + ' ' + due.format('HH:mm') : 'без времени';
                if (due && due.isBefore(moment())) when.className = 'text-danger';
                const note = document.createElement('span');
                note.textContent = task.description || task.name;
                note.style.flex = '1';
                const done = document.createElement('button');
                done.type = 'button';
                done.className = 'btn btn-default btn-sm';
                done.textContent = 'Сделано';
                done.onclick = () => this.completeCallback(task.id, done);
                item.append(when, note, done);
                list.append(item);
            });
        }

        async createCallback(form) {
            const button = form.querySelector('button');
            const due = moment.tz(form.date.value + ' ' + form.time.value, 'YYYY-MM-DD HH:mm', this.timeZone());
            if (!due.isValid()) return;
            if (due.isBefore(moment())) {
                Espo.Ui.warning('Это время уже прошло: ' + due.format('DD.MM HH:mm') + '. Проверьте дату.');
                return;
            }
            button.disabled = true;
            try {
                const company = this.model.get('accountName') || this.model.get('name');
                await Espo.Ajax.postRequest('Task', {
                    name: 'Перезвон: ' + company, status: 'Not Started',
                    parentType: 'Lead', parentId: this.model.id, parentName: company,
                    dateEnd: due.clone().utc().format('YYYY-MM-DD HH:mm:ss'),
                    description: form.note.value.trim(),
                });
                form.note.value = '';
                const today = due.isSame(moment().tz(this.timeZone()), 'day');
                Espo.Ui.success('Перезвон назначен: ' + (today ? 'сегодня' : 'на ' + due.format('DD.MM')) +
                    ' в ' + due.format('HH:mm'));
                await this.loadCallbacks();
            } finally {
                button.disabled = false;
            }
        }

        async completeCallback(id, button) {
            button.disabled = true;
            try {
                await Espo.Ajax.putRequest('Task/' + id, {status: 'Completed'});
                Espo.Ui.success('Перезвон отмечен выполненным');
                await this.loadCallbacks();
            } catch (error) {
                button.disabled = false;
            }
        }

        renderStatusButtons() {
            const host = this.element && this.element.querySelector('.middle');
            if (!host || host.querySelector('.lead-status-actions')) return;
            const bar = document.createElement('div');
            bar.className = 'lead-status-actions';
            bar.setAttribute('role', 'group');
            bar.setAttribute('aria-label', 'Статус');
            bar.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px';
            statuses.forEach(status => {
                const button = document.createElement('button');
                button.type = 'button';
                button.dataset.status = status;
                button.textContent = this.getLanguage().translateOption(status, 'status', 'Lead');
                button.onclick = () => this.changeStatus(status);
                bar.append(button);
            });
            host.prepend(bar);
            this.updateStatusButtons();
        }

        updateStatusButtons() {
            const bar = this.element && this.element.querySelector('.lead-status-actions');
            if (!bar) return;
            // Unsaved edits belong to the form; a one-click save here would mix with them.
            bar.style.display = this.mode === this.MODE_EDIT ? 'none' : 'flex';
            const current = this.model.get('status');
            bar.querySelectorAll('button').forEach(button => {
                const active = button.dataset.status === current;
                button.className = 'btn ' + (active ? 'btn-primary' : 'btn-default');
                button.setAttribute('aria-pressed', String(active));
                button.disabled = !!this.statusSaving;
            });
        }

        async changeStatus(status) {
            const previous = this.model.get('status');
            if (this.statusSaving || status === previous) return;
            this.statusSaving = true;
            this.updateStatusButtons();
            try {
                await this.model.save({status}, {patch: true});
                Espo.Ui.success(this.translate('Saved'));
            } catch (error) {
                // The CRM reports the reason itself; put the card back to the stored status.
                this.model.set('status', previous);
            } finally {
                this.statusSaving = false;
                this.updateStatusButtons();
            }
        }
    };
});
