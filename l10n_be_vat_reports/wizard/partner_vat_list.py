# Copyright 2004-2010 Tiny SPRL
# Copyright 2018 ACSONE SA/NV
# Copyright 2020 Coop IT Easy SC

from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError, Warning as UserError


class PartnerVATList(models.TransientModel):
    _name = "partner.vat.list"
    _description = "Partner VAT list"

    year = fields.Char(
        "Year",
        required=True,
        default=lambda _: str(date.today().year - 1),
    )
    limit_amount = fields.Integer("Limit Amount", required=True, default=250)
    partner_ids = fields.Many2many(
        comodel_name="partner.vat.list.client",
        string="Clients",
        help="You can remove clients/partners which you do "
        "not want to show in xml file",
    )
    declarant_reference = fields.Char(compute="_compute_declarant_reference")
    total_turnover = fields.Float("Total Turnover", compute="_compute_totals")
    total_vat = fields.Float("Total VAT", compute="_compute_totals")
    comments = fields.Text("Comments")

    def _compute_declarant_reference(self):
        self.env["ir.sequence"].next_by_code("declarantnum")
        company = self.env.company
        company_vat = company.partner_id.vat

        if not company_vat:
            raise ValidationError(_("No VAT number associated with your company."))

        company_vat = company_vat.replace(" ", "").upper()
        for listing in self:
            seq_declarantnum = self.env["ir.sequence"].next_by_code("declarantnum")
            listing.declarant_reference = company_vat[2:] + seq_declarantnum[-4:]

    @api.depends("partner_ids")
    def _compute_totals(self):
        for vat_list in self:
            vat_list.total_turnover = round(
                sum(p.turnover for p in vat_list.partner_ids), 2
            )
            vat_list.total_vat = round(
                sum(p.vat_amount for p in vat_list.partner_ids), 2
            )

    def get_partners(self):
        self.ensure_one()
        date_from = date(int(self.year), 1, 1)
        date_to = date(int(self.year), 12, 31)

        partner_vat_list_client_model = self.env["partner.vat.list.client"]
        partners = partner_vat_list_client_model.browse([])
        turnover_tags = ("00", "01", "02", "03", "45", "49")
        vat_tags = ("54", "64")
        query = """
with turnover_tag as (
    select aat.id
    from account_account_tag as aat
    inner join account_tax_report_line_tags_rel as atrltr on
        atrltr.account_account_tag_id = aat.id
    inner join account_tax_report_line as atrl on
        atrltr.account_tax_report_line_id = atrl.id and
        atrl.tag_name in %(turnover_tags)s),
vat_tag as (
    select aat.id
    from account_account_tag as aat
    inner join account_tax_report_line_tags_rel as atrltr on
        atrltr.account_account_tag_id = aat.id
    inner join account_tax_report_line as atrl on
        atrltr.account_tax_report_line_id = atrl.id and
        atrl.tag_name in %(vat_tags)s)
select
    rp.name,
    rp.vat,
    aml1.total_amount as turnover,
    coalesce(aml2.total_amount, 0.00) as vat_amount
from
    res_partner as rp
inner join (
    select
        aml.partner_id,
        round(sum(-aml.balance), 2) as total_amount
    from account_move_line as aml
    inner join account_move as am on
        aml.move_id = am.id and
        am.state = 'posted'
    where aml.date between %(date_from)s and %(date_to)s and
        aml.company_id = %(company_id)s
    and exists (
        select 1
        from account_account_tag_account_move_line_rel as aatamlr
        inner join turnover_tag as tt on
            aatamlr.account_account_tag_id = tt.id
        where aatamlr.account_move_line_id = aml.id)
    group by 1) as aml1 on
    aml1.partner_id = rp.id
left join (
    select
        aml.partner_id,
        round(sum(-aml.balance), 2) as total_amount
    from account_move_line as aml
    inner join account_move as am on
        aml.move_id = am.id and
        am.state = 'posted'
    where aml.date between %(date_from)s and %(date_to)s and
        aml.company_id = %(company_id)s
    and exists (
        select 1
        from account_tax as at
        inner join account_tax_repartition_line as atrl on
            at.id in (atrl.invoice_tax_id, atrl.refund_tax_id)
        inner join account_account_tag_account_tax_repartition_line_rel
            as aatatrlr on
            aatatrlr.account_tax_repartition_line_id = atrl.id
        inner join vat_tag as vt on
            aatatrlr.account_account_tag_id = vt.id
        where at.id = aml.tax_line_id)
    group by 1) as aml2 on
    aml2.partner_id = rp.id
where
    rp.vat ilike 'be%%' and
    aml1.total_amount >= %(limit_amount)s
        """
        args = {
            "turnover_tags": turnover_tags,
            "vat_tags": vat_tags,
            "date_from": date_from,
            "date_to": date_to,
            "company_id": self.env.company.id,
            "limit_amount": self.limit_amount,
        }
        self.env.cr.execute(query, args)
        for seq, record in enumerate(self.env.cr.dictfetchall(), start=1):
            record["vat"] = record["vat"].replace(" ", "").upper()
            record["seq"] = seq
            partners |= partner_vat_list_client_model.create(record)

        if not partners:
            raise UserError(_("No data found for the selected year."))

        model_datas = self.env["ir.model.data"].search(
            [
                ("model", "=", "ir.ui.view"),
                ("name", "=", "partner_vat_list_view_form_clients"),
            ],
            limit=1,
        )
        resource_id = model_datas.res_id
        self.partner_ids = partners.ids
        return {
            "name": _("VAT Listing"),
            "res_id": self.id,
            "view_type": "form",
            "view_mode": "form",
            "res_model": "partner.vat.list",
            "views": [(resource_id, "form")],
            "type": "ir.actions.act_window",
            "target": "inline",
        }

    def create_xml(self):
        self.ensure_one()
        return self.env.ref(
            "l10n_be_vat_reports.l10n_be_vat_listing_consignment_xml_report"
        ).report_action(self, config=False)

    def print_vatlist(self):
        self.ensure_one()

        if not self.partner_ids:
            raise UserError(_("No record to print."))

        return self.env.ref(
            "l10n_be_vat_reports.action_report_l10nvatpartnerlisting"
        ).report_action(self)
