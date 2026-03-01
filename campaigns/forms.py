from django import forms
from django.utils.text import slugify

from .models import Campaign


class CampaignForm(forms.ModelForm):
    cities_input = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        label='Cities',
        help_text='One city per line, use exact OSM names e.g. "San Jose"',
        required=True,
    )

    class Meta:
        model = Campaign
        fields = ['name', 'slug', 'goal', 'start_date', 'end_date',
                  'instructions', 'materials_url', 'contact_info']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        if self.instance and self.instance.pk and self.instance.cities:
            self.initial['cities_input'] = '\n'.join(self.instance.cities)

    def clean_cities_input(self):
        text = self.cleaned_data.get('cities_input', '')
        return [line.strip() for line in text.splitlines() if line.strip()]

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.cities = self.cleaned_data['cities_input']
        if not instance.slug:
            instance.slug = slugify(instance.name)
        if commit:
            instance.save()
        return instance
