import json

from django import forms
from django.utils.text import slugify

from .models import Campaign


class CampaignForm(forms.ModelForm):
    cities_json = forms.CharField(
        widget=forms.HiddenInput(),
        required=True,
    )

    class Meta:
        model = Campaign
        fields = ['name', 'slug', 'start_date', 'end_date',
                  'instructions', 'contact_info']
        widgets = {
            'instructions': forms.HiddenInput(attrs={'id': 'id_instructions'}),
            'contact_info': forms.TextInput(),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        if self.instance and self.instance.pk and self.instance.cities:
            self.initial['cities_json'] = json.dumps(self.instance.cities)

    def clean_instructions(self):
        html = self.cleaned_data.get('instructions', '')
        # Quill inserts &nbsp; between words, which prevents CSS word-wrapping.
        # Replace non-breaking spaces with regular spaces.
        return html.replace('&nbsp;', ' ').replace('\u00a0', ' ')

    def clean_cities_json(self):
        raw = self.cleaned_data.get('cities_json', '')
        try:
            cities = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise forms.ValidationError('Invalid city data.')
        if not isinstance(cities, list) or not cities:
            raise forms.ValidationError('At least one city is required.')
        for city in cities:
            if not isinstance(city, dict) or 'name' not in city or 'osm_id' not in city:
                raise forms.ValidationError('Each city must have a name and OSM ID.')
        return cities

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.cities = self.cleaned_data['cities_json']
        if not instance.slug:
            instance.slug = slugify(instance.name)
        if commit:
            instance.save()
        return instance
