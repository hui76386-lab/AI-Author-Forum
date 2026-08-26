class ReaderInteractionsRouter:
    app_label = "reader_interactions"
    database_alias = "interactions"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.database_alias
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.database_alias
        return None

    def allow_relation(self, obj1, obj2, **hints):
        first_is_interaction = obj1._meta.app_label == self.app_label
        second_is_interaction = obj2._meta.app_label == self.app_label
        if first_is_interaction or second_is_interaction:
            return first_is_interaction and second_is_interaction
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            return db == self.database_alias
        if db == self.database_alias:
            return False
        return None
