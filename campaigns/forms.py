import json
import os

from django import forms
from django.utils.text import slugify

from .models import Campaign, CampaignImage, ALLOWED_IMAGE_EXTENSIONS


class CampaignForm(forms.ModelForm):
    cities_json = forms.CharField(
        widget=forms.HiddenInput(),
        required=True,
    )

    class Meta:
        model = Campaign
        fields = ['name', 'slug', 'start_date', 'end_date',
                  'hero_image_url', 'instructions', 'contact_info', 'is_test']
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


class ImageUploadForm(forms.Form):
    image = forms.FileField(
        label='Image file',
        help_text='Accepted formats: JPEG, PNG, GIF, WebP. Landscape orientation (16:9) recommended.',
    )
    attest_rights = forms.BooleanField(
        required=True,
        label='I confirm that I own or have rights to use this image.',
    )
    attest_content = forms.BooleanField(
        required=True,
        label='I confirm this image contains no abusive, hateful, or illegal content, including but not limited to CSAM, incitement to violence, or harassment.',
    )

    def clean_image(self):
        f = self.cleaned_data['image']
        ext = os.path.splitext(f.name)[1].lstrip('.').lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise forms.ValidationError(
                f'Unsupported file type ".{ext}". Allowed: {", ".join(ALLOWED_IMAGE_EXTENSIONS)}.'
            )
        return f
