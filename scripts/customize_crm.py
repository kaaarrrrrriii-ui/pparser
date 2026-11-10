"""Write the EspoCRM custom layer without modifying upstream application files."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "vendor/crm-custom/Espo/Custom/Resources"


def write(path, data):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ownership_fields = {name: {"layoutAvailabilityList": []} for name in
                        ("assignedUser", "teams", "createdBy", "modifiedBy")}
    ownership_fields["assignedUser"]["required"] = False
    write("metadata/entityDefs/Lead.json", {
        "fields": {
            **ownership_fields,
            "accountName": {"required": True},
            "status": {"options": ["New", "Assigned", "In Process", "Send Offer", "Cold Email",
                                   "Converted", "Recycled", "Dead"],
                       "style": {"Assigned": "primary", "In Process": "warning", "Send Offer": "primary",
                                 "Cold Email": "primary", "Converted": "success", "Recycled": "info",
                                 "Dead": "info"}},
            "phoneNumber": {"typeList": ["Office", "Personal", "Administrator"], "defaultType": "Office"},
            "cLeadgenId": {"type": "varchar", "maxLength": 32, "clientReadOnly": True, "layoutAvailabilityList": []},
            "cWebsiteStatus": {"type": "enum", "options": ["Present", "NotListed", "Unknown"],
                               "default": "Unknown", "displayAsLabel": True,
                               "style": {"Present": "success", "NotListed": "warning", "Unknown": "default"}},
            "cCategory": {"type": "varchar", "maxLength": 255},
            "cMapSources": {"type": "text", "clientReadOnly": True},
            "cYandexUrl": {"type": "url", "clientReadOnly": True},
            "cGisUrl": {"type": "url", "clientReadOnly": True},
            "cGisCreatedAt": {"type": "datetime", "clientReadOnly": True},
            "cYandexRating": {"type": "float", "decimalPlaces": 1, "clientReadOnly": True},
            "cYandexReviews": {"type": "int", "clientReadOnly": True},
            "cGisRating": {"type": "float", "decimalPlaces": 1, "clientReadOnly": True},
            "cGisReviews": {"type": "int", "clientReadOnly": True},
            "cSocialLinks": {"type": "text"},
            "source": {"options": ["", "Maps", "Other"], "default": "Other"},
        },
        "indexes": {"leadgenId": {"columns": ["cLeadgenId"], "unique": True}},
        "collection": {"orderBy": "createdAt", "order": "desc",
                       "textFilterFields": ["accountName", "name", "addressCity", "cCategory"]},
    })
    write("metadata/clientDefs/Lead.json", {
        "controller": "controllers/record",
        "nameAttribute": "accountName",
        "views": {"detail": "views/detail", "list": "custom:views/lead-list"},
        "recordViews": {"detail": "custom:views/lead-detail", "edit": "custom:views/lead-edit"},
        "duplicateDisabled": True, "mergeDisabled": True,
        "boolFilterList": [],
        "detailActionList": [],
        "massActionList": ["remove", "massUpdate", "export"],
        "checkAllResultMassActionList": ["massUpdate", "export"],
        "defaultSidePanelDisabled": False,
        "defaultSidePanelFieldList": ["description"],
        "sidePanels": {"detail": [],
                       "detailSmall": [], "edit": [], "editSmall": []},
        "bottomPanels": {"detail": []},
    })
    detail = [{"label": "Компания", "rows": [
        [{"name": "accountName"}, {"name": "status"}],
        [{"name": "phoneNumber"}, {"name": "emailAddress"}],
        [{"name": "website"}, {"name": "cWebsiteStatus"}],
        [{"name": "addressCity"}, {"name": "addressStreet"}],
        [{"name": "cCategory", "fullWidth": True}],
        [{"name": "cSocialLinks", "fullWidth": True}],
    ]}, {"label": "Источники", "rows": [[{"name": "cYandexUrl"}, {"name": "cGisUrl"}],
                                            [{"name": "cGisCreatedAt"}],
                                            [{"name": "cYandexRating"}, {"name": "cYandexReviews"}],
                                            [{"name": "cGisRating"}, {"name": "cGisReviews"}]]}]
    for layout in ("detail", "edit"):
        write(f"layouts/Lead/{layout}.json", detail if layout == "detail" else detail[:1])
    write("layouts/Lead/list.json", [
        {"name": "accountName", "link": True, "width": 25}, {"name": "status", "width": 12},
        {"name": "phoneNumber", "width": 15}, {"name": "emailAddress", "width": 17},
        {"name": "cWebsiteStatus", "width": 12}, {"name": "website", "width": 19},
        {"name": "cYandexRating", "width": 12}, {"name": "cYandexReviews", "width": 12},
        {"name": "cGisRating", "width": 12}, {"name": "cGisReviews", "width": 12},
    ])
    write("layouts/Lead/filters.json", ["cWebsiteStatus", "addressCity", "cCategory"])
    write("layouts/Lead/sidePanelsDetail.json", {"default": {"index": 0}})
    write("layouts/Lead/bottomPanelsDetail.json", {"stream": {"disabled": True}})
    write("layouts/Lead/relationships.json", [])
    write("metadata/scopes/Lead.json", {"stream": False})
    write("metadata/entityDefs/Task.json", {"fields": {**ownership_fields,
          "collaborators": {"layoutAvailabilityList": []}}})
    write("metadata/clientDefs/Task.json", {"defaultSidePanelDisabled": True, "boolFilterList": [],
                                           "sidePanels": {"detail": [], "edit": []}})
    write("metadata/clientDefs/User.json", {"recordViews": {"detail": "custom:views/user-detail"}})
    write("metadata/scopes/Task.json", {"stream": False})
    task_detail = [{"label": "Задача", "rows": [
        [{"name": "name"}, {"name": "parent"}],
        [{"name": "status"}, {"name": "priority"}],
        [{"name": "dateStart"}, {"name": "dateEnd"}],
        [{"name": "description", "fullWidth": True}],
    ]}]
    for layout in ("detail", "edit"):
        write(f"layouts/Task/{layout}.json", task_detail)
    write("layouts/Task/list.json", [{"name": "name", "link": True}, {"name": "status", "width": 16},
                                    {"name": "priority", "width": 14}, {"name": "dateEnd", "width": 20}])
    write("layouts/Task/filters.json", ["name", "status", "priority", "parent", "dateStart", "dateEnd"])
    write("layouts/Task/relationships.json", [])
    write("layouts/Task/bottomPanelsDetail.json", {"stream": {"disabled": True}})
    write("metadata/clientDefs/App.json", {"loginView": "custom:views/local-workspace",
                                          "navbarView": "custom:views/local-navbar"})
    write("metadata/clientDefs/Home.json", {"controller": "custom:controllers/local-home"})
    write("metadata/app/api.json", {"globalMiddlewareClassNameList": [
        "Espo\\Custom\\Classes\\Api\\LocalWorkspace"]})
    write("metadata/app/clientNavbar.json", {"items": {"notificationBadge": {"disabled": True},
                                                      "lastViewed": {"disabled": True}}})
    write("i18n/ru_RU/Lead.json", {
        "fields": {"accountName": "Компания", "description": "Заметки менеджера", "source": "Источник",
                   "cWebsiteStatus": "Наличие сайта", "cCategory": "Категория",
                   "cMapSources": "Карточки на картах", "cSocialLinks": "Соцсети и мессенджеры",
                   "cYandexUrl": "Яндекс.Карты", "cGisUrl": "2ГИС",
                   "cGisCreatedAt": "Внесена в базу 2ГИС",
                   "cYandexRating": "Оценка Яндекс.Карт", "cYandexReviews": "Оценок Яндекс.Карт",
                   "cGisRating": "Оценка 2ГИС", "cGisReviews": "Оценок 2ГИС"},
        "options": {"cWebsiteStatus": {"Present": "Сайт указан", "NotListed": "Сайт не указан", "Unknown": "Не проверено"},
                    "source": {"Maps": "Карты", "Other": "Вручную"},
                    "status": {"New": "Новый", "Assigned": "Назначен", "In Process": "В работе",
                               "Send Offer": "Отправить оффер", "Cold Email": "Cold-email", "Converted": "Успешно",
                               "Recycled": "Перезвонить позже", "Dead": "Отказ"},
                    "phoneNumber": {"Office": "Не уточнён", "Personal": "Личный",
                                    "Administrator": "Администратор"}},
        "tooltips": {"cWebsiteStatus": "Не указан: ссылки нет в собранных карточках. Это не доказывает отсутствие сайта."},
    })
    write("i18n/en_US/Lead.json", {
        "fields": {"accountName": "Company", "cWebsiteStatus": "Website", "cCategory": "Category",
                   "cMapSources": "Map sources", "cSocialLinks": "Social links",
                   "cGisCreatedAt": "Added to 2GIS catalog"},
        "options": {"cWebsiteStatus": {"Present": "Listed", "NotListed": "Not listed", "Unknown": "Unknown"},
                    "source": {"Maps": "Maps"}},
    })
    write("metadata/app/consoleCommands.json", {"leadgenSetup": {
        "className": "Espo\\Custom\\Classes\\ConsoleCommands\\LeadgenSetup", "listed": True,
        "allowedOptions": [], "allowedFlags": [],
    }})


if __name__ == "__main__":
    main()
